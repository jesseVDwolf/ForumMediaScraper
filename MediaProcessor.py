import re
import requests
import bs4
import gridfs
import logging
from requests.exceptions import RequestException
from datetime import datetime, timedelta

from pymongo.database import Database
from pymongo.results import InsertOneResult


class AlreadyProcessedException(Exception):
    """Raised when the MediaProcessor finds an article that it has already processed"""
    pass


class MediaProcessor:
    """
    NOTE: The words "Article" and "Post" are used interchangeably
    The 9gag forums contain 3 type of media: picture, gif, video,
    external link this version of the MediaProcessor only supports
    processing of single pictures but future version may include
    support for gifs, videos and external links

    Web based structures are as follows:
    * pictures: <article><div class="post-container"><div class="post-container"><picture>
                <article><div class="post-container"><div class="post-container with-button"><picture>
    * gif:      <article><div class="post-container"><div class="post-view gif-post"><video>
    * video:    <article><div class="post-container"><div class="post-view video-post"><video>
    * external: ?
    """

    def __init__(self, scraper_run_id: int, db: Database, fs: gridfs.GridFS):
        self.run_id = scraper_run_id
        self.mongo_db = db
        self.grid_fs = fs
        self.articles_processed = 0
        self.FORUM_ARTICLE_TYPES = [
            'post-container-with-button',   # https://9gag.com/gag/a7wwAyL
            'post-container',               # https://9gag.com/gag/aMYYqd6
            'post-view-video-post',         # https://9gag.com/gag/aKddO1Q
            'post-view-gif-post'            # https://9gag.com/gag/aAggOvp
        ]
        self.ARTICLE_OPTIONS = {}
        self.stop_reason = 'Something went wrong during processing'

        # create settings for choosing processor function
        [self.ARTICLE_OPTIONS.update({i: '_process_' + i.replace('-', '_')}) for i in self.FORUM_ARTICLE_TYPES]

        # retrieve article id of the article where the last run started
        last_runs = self.mongo_db['Runs'].find({'StartPostId': {'$ne': None}}).sort('_id', -1).limit(1)
        self.last_start_article_id = str(last_runs[0].get('StartPostId')) if last_runs.count() > 0 else None

    def __del__(self):
        logging.info('Crawler has finished scraping for reason: {}. Sending last data to database...'.format(self.stop_reason))
        self.mongo_db['Runs'].update_one(
            {'_id': self.run_id},
            {'$set':
                 {
                     'EndScrapeTime': datetime.utcnow(),
                     'PostsProcessed': self.articles_processed
                 }
            }
        )

    @staticmethod
    def create_stream_list_regex(stream_id: str):
        """
        Given an id of a number smaller than 100, this function will give back
        compiled regex that can be used to match any number above this id.
        :param stream_id:
        :return <class '_sre.SRE_Pattern'>:
        """

        # match the amount of numbers in stream id + 1 or more digit numbers
        base_regex = r'[1-9]\d{%s,}' % str((len(stream_id)))

        # if single digit and nine then base is enough
        if len(stream_id) == 1 and int(stream_id) == 9:
            return re.compile(base_regex)

        # if single digit then add special regex
        elif len(stream_id) == 1 and int(stream_id) != 9:
            base_regex = base_regex + '|[%s-9]' % str(int(stream_id) + 1)
            return re.compile(base_regex)

        for idx, num in enumerate(stream_id):
            if num == '9':
                # round can be skipped since range contains no numbers
                continue
            elif idx == 0:
                # match all numbers from {x}0 to 99 i.e. for 88 -> 89,90,91,92...99
                base_regex = base_regex + '|[{x}-9]\d'.format(
                    x=str(int(stream_id[stream_id.find(num)]) + 1)
                )
            elif idx == 1:
                # match all numbers within the single digit range i.e. for 56 -> 57,58,59
                base_regex = base_regex + '|{x}[{y}-9]'.format(
                    x=str(int(stream_id[stream_id.find(num) - 1])),
                    y=str(int(num) + 1)
                )

        return re.compile('stream-' + base_regex)

    def process(self, article: bs4.element.Tag):
        """
        This function acts as the base processor function and does checks on
        the article html data. It will then try to find a processor function
        based on the article type.
        :param article:
        :return:
        """
        article_container = article.find('div', {'class': 'post-container'})

        # check for sensitive content (requires login)
        if article_container.find('div', {'class': 'nsfw-post'}):
            logging.info('Sensitive article found, forum login not yet supported')
            return

        # check for empty articles and skip these
        if not article.get('id'):
            logging.info('Empty article found, moving to next article')
            return

        article_id = str(article.get('id')).strip()
        article_short = str(article_container.a.get('href'))
        article_type = '-'.join(article_container.find('div').find('div').get('class'))

        # check if article has been processed already in the past
        # if it finds an already processed article, raise a custom exception and stop the scraper
        # since from that point on all articles have been scraped
        if article_id == self.last_start_article_id:
            self.stop_reason = 'Found article that was already processed: {}'.format(article_short)
            raise AlreadyProcessedException

        try:
            # use appropriate processing function to process the article data
            result = eval('self.{func}'.format(func=self.ARTICLE_OPTIONS[article_type]))(article)

            # if first article processed update the StartPostId field in the Runs collection
            if self.articles_processed == 0 and result.acknowledged:
                self.mongo_db['Runs'].update_one(
                    {'_id': self.run_id},
                    {'$set':
                         {
                             'StartPostId': article_id
                         }
                    }
                )

            # update articles processed if processing was successful
            self.articles_processed = self.articles_processed + 1 if result.acknowledged else self.articles_processed

        except AttributeError:
            logging.info('Processing of {} is in option list but not yet supported, skipping article {}'.format(article_type, article_short))
        except KeyError:
            logging.info('Processing of {} is not in option list, skipping article'.format(article_type, article_short))
        except Exception as InternalError:
            logging.warning('Something went wrong during processing of article {}: {}'.format(article_short, InternalError))

    def _process_post_container(self, article: bs4.element.Tag) -> InsertOneResult:
        """
        This function processes article data from a 9Gag article. Specifically that
        of a single picture. Processing consists of two main steps. Retrieving needed
        data from Soup object and saving this data to the MongoDB database.
        :param article:
        :return:
        """
        image_source = 'None'

        try:
            # get metadata for GridFS storage of the picture
            image_source = str(article.find('picture').find('img')['src'])
            file_name = image_source[image_source.rfind('/') + 1: len(image_source)]
            metadata = {
                'Filename': file_name,
                'FileType': file_name[file_name.rfind('.') + 1: len(file_name)],
                'SourceURL': image_source,
                'MediaType': 'post-container'
            }
            response = requests.get(image_source)
            response.raise_for_status()

            # store image in mongodb using gridfs filesystem
            media_id = self.grid_fs.put(response.content, **metadata)

            # build document for the Posts collection
            header = article.find('header')
            message = header.find('div', {'class': 'post-section'}).find('p', {'class': 'message'})
            message_text = message.get_text().split('·')  # sample text: ' Video  · 2h'
            hour_created = str(message_text[1]).strip()
            hour_created_date = datetime.utcnow()

            if hour_created[-1] == 'h':
                hour_created_date -= timedelta(hours=int(hour_created[:-1]))
            if hour_created[-1] == 'd':
                hour_created_date -= timedelta(days=int(hour_created[:-1]))

            post_meta = article.find('p', {'class': 'post-meta'})  # sample text: ' 1,758 points  ·  55 comments '
            post_meta_text = [''.join(char for char in string if char.isdigit()) for string in
                              post_meta.get_text().split('·')]
            post_short_link = str(post_meta.a.get('href'))
            post_document = {
                'ArticleId': article.get('id'),
                'Title': str(header.find('h1').get_text()),
                'Section': str(message_text[0]).strip(),
                'HourCreated': hour_created,
                'HourCreatedDate': hour_created_date,
                'Points': int(post_meta_text[0]),
                'Comments': int(post_meta_text[1]),
                'PostShortLink': post_short_link,
                'ProcessTime': datetime.utcnow(),
                'MediaId': media_id,
                'RunId': self.run_id
            }

            # store post data in mongodb collection
            result = self.mongo_db['Posts'].insert_one(post_document)
            logging.info('Article {} has been successfully processed and saved the database'.format(post_short_link))
            return result

        except RequestException:
            logging.warning('Could not retrieve image {} during processing'.format(image_source))
            return None
