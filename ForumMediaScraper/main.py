import os
import re
import sys
import time
import requests
import bs4
import gridfs
import logging
import urllib.parse
from requests.exceptions import RequestException
from datetime import datetime, timedelta

from selenium import webdriver as SeleniumWebdriver
from selenium.common.exceptions import WebDriverException as SeleniumWebDriverException

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError as MongoServerSelectionTimeoutError

from .MediaProcessor import MediaProcessor, AlreadyProcessedException


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


class ForumMediaScraper:
    """
    Main media scraper singleton class
    """
    def __init__(self):
        self.MONGO_INITDB_ROOT_USERNAME = os.getenv('MONGO_INITDB_ROOT_USERNAME')
        self.MONGO_INITDB_ROOT_PASSWORD = os.getenv('MONGO_INITDB_ROOT_PASSWORD')
        self.MAX_SERVER_SELECTION_DELAY = 1

        self.FORUM_HOME_PAGE_URL = "https://9gag.com/hot"
        self.GECKO_DRIVER_PATH = 'ForumMediaScraper\\bin\\geckodriver.exe'

        self.SCROLL_PAUSE_TIME = 0.5
        self.MAX_SCROLL_SECONDS = os.getenv('MAX_SCROLL_SECONDS') if os.environ.get('MAX_SCROLL_SECONDS') else "60"
        self.LOGGING_TYPE = os.getenv('LOGGING_TYPE') if os.environ.get('LOGGING_TYPE') else 'default'

        # database related objects
        self.mongo_client = MongoClient
        self.database = Database

        # set up service logging
        self.logger = logging.getLogger(__name__)
        logging_args = {
            "format": '%(asctime)s %(levelname)-8s %(message)s',
            "level": logging.INFO,
            "datefmt": '%Y-%m-%d %H:%M:%S'
        }
        if os.environ.get('LOGGING_TYPE') == 'file':
            logging_args.update({'filename': './ForumMediaScraper/log/service.log'})
        if not os.path.isdir('ForumMediaScraper/log'):
            os.mkdir('ForumMediaScraper/log')
        logging.basicConfig(**logging_args)
        self.logger.info('###############FORUM MEDIA SCRAPER INITIALIZATION###############')
        self._validate_scrape_conditions()

    def _validate_scrape_conditions(self):
        # check if forum is online and accessable
        try:
            response = requests.get(self.FORUM_HOME_PAGE_URL)
            response.raise_for_status()
            self.logger.info('{} is only and available for scraping'.format(self.FORUM_HOME_PAGE_URL))
        except RequestException:
            self.logger.error('Forum {} is not reachable, can not start scraper'.format(self.FORUM_HOME_PAGE_URL))
            sys.exit(1)

        # check if environment is set up correctly
        if not self.MONGO_INITDB_ROOT_PASSWORD or not self.MONGO_INITDB_ROOT_USERNAME:
            self.logger.error('Environment not setup correctly, are all environment variables set up?')
            sys.exit(1)

        if not self.MAX_SCROLL_SECONDS.isdigit():
            self.logger.error('MAX_SCROLL_SECONDS must be digit')
            sys.exit(1)

        self.logger.info('''\n
            Environment set up with the following settings: 
            - MONGO_INITDB_ROOT_USERNAME = {} 
            - MONGO_INITDB_ROOT_PASSWORD = {} 
            - MAX_SCROLL_SECONDS = {} 
            - LOGGING_TYPE = {} 
        '''.format(self.MONGO_INITDB_ROOT_USERNAME, self.MONGO_INITDB_ROOT_PASSWORD, str(self.MAX_SCROLL_SECONDS), self.LOGGING_TYPE))

        try:
            self.logger.info('Trying to connect to local MongoDB instance..')
            #  create mongo client to interact with local mongoDB instance
            self.mongo_client = MongoClient('mongodb://{usr}:{pwd}@127.0.0.1'.format(
                usr=urllib.parse.quote_plus(self.MONGO_INITDB_ROOT_USERNAME),
                pwd=urllib.parse.quote_plus(self.MONGO_INITDB_ROOT_PASSWORD)),
                serverSelectionTimeoutMS=self.MAX_SERVER_SELECTION_DELAY
            )

            # force connection on a request to check if server is online
            self.mongo_client.server_info()
            self.database = self.mongo_client['9GagMedia']
            self.mongo_gridfs = gridfs.GridFS(database=self.database)
            self.logger.info('MongoDB connection setup successfully, ready to store data')

        except MongoServerSelectionTimeoutError as serverTimeout:
            self.logger.error('Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))
            sys.exit(1)

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

    def start_scraper(self):
        try:
            self.logger.info('Configuring gecko selenium webdriver for python..')
            web_driver_args = {
                "executable_path": r'{}\{}'.format(os.getcwd(), self.GECKO_DRIVER_PATH),
                "log_path": './ForumMediaScraper/log/geckodriver.log'
            }

            # try to set up firefox driver for selenium and retrieve forum home page
            with _WebDriver(SeleniumWebdriver.Firefox(**web_driver_args)) as wd:
                wd.get(self.FORUM_HOME_PAGE_URL)
                self.logger.info('Successfully loaded {}'.format(self.FORUM_HOME_PAGE_URL))
                self.logger.info('###############FORUM MEDIA SCRAPER SCRAPE LOG###############')

                last_height = wd.execute_script("return document.body.scrollHeight")
                start_time = datetime.utcnow()

                # create run entry for scraper in mongo database
                result = self.database['Runs'].insert_one({
                    'StartScrapeTime': datetime.utcnow(),
                    'EndScrapeTime': None,
                    'PostsProcessed': 0,
                    'StartPostId': None
                })

                """
                The 9gag forums are build up using javascript list streams <div id="streams-x"> with a max of
                5 <article id="jsid-post-xxxxxxx"> posts per list stream. Using the list stream ids you can start 
                the scraping where the scraper last ended since list stream items are added while the page scrolls down.
                """
                stream_tracker = []
                media_processor = MediaProcessor(
                    scraper_run_id=result.inserted_id,
                    db=self.database,
                    fs=self.mongo_gridfs,
                    logger=self.logger
                )

                try:
                    while True:
                        # Scroll down to bottom to load all possible posts for this scrape cycle
                        wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")

                        # Wait to load page
                        time.sleep(self.SCROLL_PAUSE_TIME)

                        # build regex search for stream using last know stream id
                        last_stream_id = stream_tracker[-1] if len(stream_tracker) > 0 else '0'
                        regex = self._create_stream_list_regex(stream_id=last_stream_id)

                        # create BeautifullSoup object for easier access to html data
                        soup = bs4.BeautifulSoup(wd.page_source, 'html.parser')
                        for list_stream in soup.find_all('div', {'id': regex}):

                            # add id to stream tracker
                            stream_id = str(list_stream['id'])
                            stream_tracker.append(stream_id[stream_id.find('-') + 1:len(stream_id)])

                            for article in list_stream.find_all('article'):
                                media_processor.process(article=article)

                        # Calculate new scroll height and compare with last scroll height
                        new_height = wd.execute_script("return document.body.scrollHeight")
                        if new_height == last_height or start_time + timedelta(
                                seconds=int(self.MAX_SCROLL_SECONDS)) < datetime.utcnow():
                            break
                        last_height = new_height

                    # clean up when the MediaScraper has finished
                    media_processor.stop_reason = "Reached max scroll seconds limit"
                    del media_processor

                except AlreadyProcessedException:
                    del media_processor
                    sys.exit(1)

        except SeleniumWebDriverException as driverException:
            self.logger.error('Could not create firefox driver using local geckodriver: {err}'.format(err=driverException.msg))
            sys.exit(1)
        except MongoServerSelectionTimeoutError as serverTimeout:
            self.logger.error('Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))
            sys.exit(1)


if __name__ == '__main__':
    media_scraper = ForumMediaScraper()
    media_scraper.start_scraper()
