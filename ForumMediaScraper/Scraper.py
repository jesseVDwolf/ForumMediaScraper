import os
import re
import sys
import bs4
import time
import gridfs
import logging
import requests
from datetime import (
    datetime,
    timedelta
)
from bson import ObjectId
from pymongo import MongoClient
from requests.exceptions import RequestException
from selenium import webdriver as SeleniumWebdriver
from pymongo.errors import ServerSelectionTimeoutError as MongoServerSelectionTimeoutError

WEBDRIVER_DEFAULT_PATH = 'geckodriver'
WEBDRIVER_DEFAULT_LOGDIR = './log/geckodriver.log'

MONGO_SERVER_TIMEOUT = 1

SCRAPER_DEFAULT_MAX_SCROLL_SECONDS = 20
SCRAPER_DEFAULT_FORUM = '9gag'

_SCRAPER_SCROLL_PAUSE_TIME = 1.5
_SCRAPER_SUPPORTED_FORUMS = [{
    'name': '9gag',
    'home_page_url': 'https://9gag.com',
    'processors': [
        'post-container-with-button',   # https://9gag.com/gag/a7wwAyL
        'post-container',               # https://9gag.com/gag/aMYYqd6
        'post-view-video-post',         # https://9gag.com/gag/aKddO1Q
        'post-view-gif-post'            # https://9gag.com/gag/aAggOvp
    ]
}]

_SCRAPER_ASCII_ART = """

                  ______                            _____                                
                 |  ____|                          / ____|                               
                 | |__ ___  _ __ _   _ _ __ ___   | (___   ___ _ __ __ _ _ __   ___ _ __ 
                 |  __/ _ \| '__| | | | '_ ` _ \   \___ \ / __| '__/ _` | '_ \ / _ \ '__|
                 | | | (_) | |  | |_| | | | | | |  ____) | (__| | | (_| | |_) |  __/ |   
                 |_|  \___/|_|   \__,_|_| |_| |_| |_____/ \___|_|  \__,_| .__/ \___|_|   
                                                                        | |              
                                                                        |_|              

---------------------------------------------------[0.1]---------------------------------------------------
"""


class ScraperConfig:

    _SCRAPER_SETTINGS = {
        'MONGO_INITDB_ROOT_USERNAME': (str, ''),
        'MONGO_INITDB_ROOT_PASSWORD': (str, ''),
        'MONGO_INITDB_HOST': (str, 'localhost'),
        'MONGO_INITDB_PORT': (int, 27017),

        'SCRAPER_FORUM_NAME': (str, SCRAPER_DEFAULT_FORUM),
        'SCRAPER_MAX_SCROLL_SECONDS': (str, SCRAPER_DEFAULT_MAX_SCROLL_SECONDS),
        'SCRAPER_CREATE_LOGFILE': (bool, False),
        'SCRAPER_HEADLESS_MODE': (bool, True),

        'WEBDRIVER_LOGDIR': (str, WEBDRIVER_DEFAULT_LOGDIR),
        'WEBDRIVER_EXECUTABLE_PATH': (str, WEBDRIVER_DEFAULT_PATH),
        'WEBDRIVER_BROWSER_EXECUTABLE_PATH': (str, None)
    }

    _iter_index = 0

    def __init__(self, config: dict={}):
        self._config = {}
        for key, value in self._SCRAPER_SETTINGS.items():
            if os.getenv(key):
                self._config[key] = value[0](os.getenv(key)) if os.getenv(key) != "None" else None
            elif config.get(key):
                self._config[key] = config[key]
            else:
                self._config[key] = value[1]

    def __getitem__(self, item):
        return self._config[item]

    def __setitem__(self, key, value):
        self._config[key] = value

    def __iter__(self):
        return self

    def __next__(self):
        idx = self._iter_index
        if idx >= len(self._config):
            self._iter_index = 0
            raise StopIteration()
        self._iter_index = idx + 1
        return list(self._config.keys())[idx], list(self._config.values())[idx]

    def update(self, d: dict):
        self._config.update(d)
        return self

    def get_mongo_config(self) -> dict:
        return ({
            'host': self._config['MONGO_INITDB_HOST'],
            'port': self._config['MONGO_INITDB_PORT'],
            'username': self._config['MONGO_INITDB_ROOT_USERNAME'],
            'password': self._config['MONGO_INITDB_ROOT_PASSWORD'],
            'serverSelectionTimeoutMS': MONGO_SERVER_TIMEOUT
        })

    def get_webdriver_config(self) -> dict:
        return ({
            "executable_path": self._config['WEBDRIVER_EXECUTABLE_PATH'],
            "log_path": WEBDRIVER_DEFAULT_LOGDIR,
            "firefox_binary": self._config['WEBDRIVER_BROWSER_EXECUTABLE_PATH']
        })


class _WebDriver:
    """
    Use a context manager for the selenium webdriver to make sure that
    the driver quits when the program exists for some reason
    """
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.driver.quit()


class SeleniumScraper(object):

    def __init__(self, config: ScraperConfig, log_level: int=logging.DEBUG):
        if sys.platform not in ['darwin', 'linux', 'win32']:
            raise OSError('Unsupported operating system %s' % str(sys.platform))

        self.config = config
        self.forum_config = next((f for f in _SCRAPER_SUPPORTED_FORUMS if f['name'] == self.config['SCRAPER_FORUM_NAME']), None)
        self.logger = logging.getLogger(__name__)

        self._mongo_client = MongoClient(**self.config.get_mongo_config())
        self._mongo_database = self._mongo_client['ForumMediaData']
        self._mongo_gridfs = gridfs.GridFS(database=self._mongo_database)
        self._current_run = None

        if not os.path.isdir('log'):
            os.mkdir('log')
        self._setup_logger(self.logger, self.config['SCRAPER_CREATE_LOGFILE'], log_level)

        if self.config['SCRAPER_HEADLESS_MODE']:
            os.environ['MOZ_HEADLESS'] = '1'
        print(self.config.get_webdriver_config())
        self._webdriver = SeleniumWebdriver.Firefox(**self.config.get_webdriver_config())

    @staticmethod
    def _setup_logger(logger, create_log_file, log_level):
        logger.setLevel(log_level)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch = logging.StreamHandler()
        ch.setLevel(log_level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # create service log file if specified
        if create_log_file:
            fh = logging.FileHandler('./log/scraper.log')
            fh.setLevel(log_level)
            ch.setFormatter(formatter)
            logger.addHandler(fh)

    @staticmethod
    def _create_stream_list_regex(stream_id: str):
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
            return re.compile('stream-' + base_regex)

        # if single digit then add special regex
        elif len(stream_id) == 1 and int(stream_id) != 9:
            base_regex = base_regex + '|[%s-9]' % str(int(stream_id) + 1)
            return re.compile('stream-' + base_regex)

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

    def run(self):
        try:
            with _WebDriver(self._webdriver) as wd:
                self.logger.info(_SCRAPER_ASCII_ART)
                max_scroll_seconds = self.config['SCRAPER_MAX_SCROLL_SECONDS']
                start_time = datetime.utcnow()
                stream_tracker = []

                wd.get(self.forum_config.get('home_page_url'))
                last_height = wd.execute_script('return document.body.scrollHeight')

                # create run entry for scraper in mongo database
                self._current_run = self._mongo_database['Runs'].insert_one({
                    'StartScrapeTime': datetime.utcnow(),
                    'EndScrapeTime': None,
                    'PostsProcessed': 0,
                    'StartPostId': None
                })
                processor = Processor(scraper=self)
                while True:
                    # Scroll down to bottom to load all possible posts for this scrape cycle
                    wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")

                    # wait for page to load
                    time.sleep(_SCRAPER_SCROLL_PAUSE_TIME)

                    # build regex search for stream using last known stream id
                    last_stream_id = stream_tracker[-1] if len(stream_tracker) > 0 else 0
                    regex = self._create_stream_list_regex(str(last_stream_id))

                    soup = bs4.BeautifulSoup(wd.page_source, 'html.parser')
                    for list_stream in soup.find_all('div', {'id': regex}):

                        stream_id = str(list_stream['id'])
                        stream_tracker.append(stream_id[stream_id.find('-') + 1:len(stream_id)])

                        for article in list_stream.find_all('article'):
                            processor.process(article)

                    new_height = wd.execute_script('return document.body.scrollHeight')
                    if new_height == last_height or (start_time + timedelta(seconds=max_scroll_seconds)) < datetime.utcnow():
                        break
                    last_height = new_height

                # clean up when the MediaScraper has finished
                processor.stop_reason = "Reached max scroll seconds limit"
                del processor

        except MongoServerSelectionTimeoutError as serverTimeout:
            self.logger.error('[SeleniumScraper] Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))


class Processor(object):

    def __init__(self, scraper: SeleniumScraper):
        self.scraper = scraper
        self.articles_processed = 0
        self.logger = scraper.logger
        self.stop_reason = 'Something went wrong during processing'

    def __del__(self):
        self.logger.info('[Processor] Crawler has finished scraping for reason: {}. Sending last data to database...'.format(self.stop_reason))
        self.scraper._mongo_database['Runs'].update_one(
            {'_id': self.scraper._current_run.inserted_id},
            {'$set':
                 {
                    'EndScrapeTime': datetime.utcnow(),
                    'PostsProcessed': self.articles_processed
                 }
            }
        )

    def _article_exists(self, article_id: str) -> bool:
        runs = self.scraper._mongo_database['Runs'].find({}).sort('_id', -1)
        for run in runs:
            posts = self.scraper._mongo_database['Posts'].find({'RunId': ObjectId(run['_id'])}).sort('_id', -1)
            for post in posts:
                if post['ArticleId'] == article_id:
                    return True
        return False

    def process(self, article):
        if not article.get('id'):
            self.logger.info('[Processor] Empty article found, moving to next article')
            return

        article_container = article.find('div', {'class': 'post-container'})
        if article_container.find('div', {'class': 'nsfw-post'}):
            self.logger.info('[Processor] Sensitive article found, forum login not yet supported')
            return

        article_id = str(article.get('id')).strip()
        article_short = str(article_container.a.get('href'))
        if self._article_exists(article_id):
            self.logger.info('[Processor] Found already processed article {}'.format(article_short))
            return

        article_type = '-'.join(article_container.find('div').find('div').get('class'))
        try:
            # create processor options
            process_options = {}
            [process_options.update({i: '_process_' + i.replace('-', '_')}) for i in self.scraper.forum_config['processors']]

            # execute appropriate processing function
            func = process_options[article_type]
            result = eval('self.{func}'.format(func=func))(article)

            # if its the first article processed update the StartPostId field for this run
            if self.articles_processed == 0 and result.acknowledged:
                self.scraper._mongo_database['Runs'].update_one(
                    {'_id': self.scraper._current_run.inserted_id},
                    {'$set':
                         {
                             'StartPostId': article_id
                         }
                    }
                )
            self.articles_processed = self.articles_processed + 1 if result.acknowledged else self.articles_processed

        except AttributeError:
            self.logger.info('[Processor] Processing of {} is in option list but not yet supported, skipping article {}'.format(
                article_type,
                article_short)
            )
        except KeyError:
            self.logger.info('[Processor] Processing of {} is not in option list, skipping article'.format(
                article_type,
                article_short)
            )

    def _process_post_container(self, article: bs4.element.Tag):
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
            pics = [pic for pic in article.find_all('picture') if pic.find('img').get('style')]
            image_source = str(pics[0].find('img').get('src'))
            file_name = image_source[image_source.rfind('/') + 1: len(image_source)]
            metadata = {
                'Filename': file_name,
                'FileType': file_name[file_name.rfind('.') + 1: len(file_name)],
                'SourceURL': image_source,
                'MediaType': 'post-container'
            }
            response = requests.get(image_source)
            response.raise_for_status()

            self.logger.debug('[Processor] Found image at %s' % image_source)

            # store image in mongodb using gridfs filesystem
            media_id = self.scraper._mongo_gridfs.put(response.content, **metadata)

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
                'RunId': self.scraper._current_run.inserted_id
            }

            # store post data in mongodb collection
            result = self.scraper._mongo_database['Posts'].insert_one(post_document)
            self.logger.info(
                '[Processor] Article {} has been successfully processed and saved the database'.format(post_short_link))
            return result

        except RequestException:
            self.logger.warning('[Processor] Could not retrieve image {} during processing'.format(image_source))
            return None
