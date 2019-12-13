import os
import re
import sys
import time
import requests
import bs4
import gridfs
import logging
from requests.exceptions import RequestException
from datetime import datetime, timedelta

from selenium import webdriver as SeleniumWebdriver
from selenium.common.exceptions import WebDriverException as SeleniumWebDriverException

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError as MongoServerSelectionTimeoutError

from .MediaProcessor import MediaProcessor, AlreadyProcessedException


class ScrapeConditionsNotMetException(Exception):
    """
    Raised when one of the conditions for the scraper to be able
    to scrape is not met. Thrown from _check_scrape_conditions method
    """
    pass


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


class ForumMediaScraper(object):
    """
    Main media scraper as singleton class.
    The scraper exposes its configuration settings. This can be updated through the update_config function.
    """
    _WEBDRIVER_DEFAULT_PATH = './geckodriver'

    _MONGO_SERVER_TIMEOUT = 1

    _SCRAPER_SCROLL_PAUSE_TIME = 0.5
    _SCRAPER_FORUM_HOME_PAGE_URL = "https://9gag.com/hot"
    _SCRAPER_OPTIONAL_SETTINGS = {
        'MONGO_INITDB_ROOT_USERNAME': str,
        'MONGO_INITDB_ROOT_PASSWORD': str,
        'MONGO_INITDB_HOST': str,
        'MONGO_INITDB_PORT': int,
        'SCRAPER_MAX_SCROLL_SECONDS': int,
        'SCRAPER_CREATE_SERVICE_LOG': bool,
        'SCRAPER_HEADLESS_MODE': bool,
        'WEBDRIVER_EXECUTABLE_PATH': str,
        'WEBDRIVER_BROWSER_EXECUTABLE_PATH': str
    }

    def __init__(self, config: dict={}):
        if sys.platform not in ['darwin', 'linux', 'win32']:
            raise OSError('Unsupported operating system %s' % str(sys.platform))

        # create default configuration and update if additional config was given
        self._config = {'SCRAPER_MAX_SCROLL_SECONDS': 60, 'WEBDRIVER_EXECUTABLE_PATH': ForumMediaScraper._WEBDRIVER_DEFAULT_PATH}
        self._validate_config(config)
        self.update_config(config=config)

        # create database related objects
        self._mongo_client = MongoClient
        self._mongo_database = Database

        # create log directory
        if not os.path.isdir('log'):
            os.mkdir('log')

        # set MOZ_HEADLESS if SCRAPER_HEADLESS_MODE is set to True
        if self._config.get('SCRAPER_HEADLESS_MODE'):
            os.environ['MOZ_HEADLESS'] = '1'

        # set up service logging
        self.logger = logging.getLogger(__name__)
        logging_args = {
            "format": '%(asctime)s %(levelname)-8s %(message)s',
            "level": logging.INFO,
            "datefmt": '%Y-%m-%d %H:%M:%S'
        }
        if self._config.get('scraper_create_service_log'):
            logging_args.update({'filename': './log/service.log'})
        logging.basicConfig(**logging_args)

    @staticmethod
    def _validate_config(d: dict):
        """
        Check if the configuration specified for the scraper is
        given in the correct format and given options are valid options
        :param d:
        :return:
        """
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    raise TypeError('No nested dicts allowed for config options')
                if k not in ForumMediaScraper._SCRAPER_OPTIONAL_SETTINGS.keys():
                    raise NameError('No such option exists: %s' % str(k))
                if not isinstance(v, ForumMediaScraper._SCRAPER_OPTIONAL_SETTINGS.get(k)):
                    raise TypeError('Option %s it\'s data type does not match expected type: %s' % (str(k), str(v)))
        else:
            raise TypeError('Use a dict to update the scraper its configuration')

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

    def get_config(self):
        return self._config

    def update_config(self, config: dict):
        self._validate_config(config)
        return self._config.update(config)

    def _check_scrape_conditions(self):
        try:
            # forum must be online and available for scraping
            response = requests.get(ForumMediaScraper._SCRAPER_FORUM_HOME_PAGE_URL)
            response.raise_for_status()
            self.logger.info('%s is online and available for scraping' % ForumMediaScraper._SCRAPER_FORUM_HOME_PAGE_URL)

            #  create mongo client to interact with local mongoDB instance
            connection_args = {
                'host': None,
                'serverSelectionTimeoutMS': ForumMediaScraper._MONGO_SERVER_TIMEOUT
            }

            if self._config.get('MONGO_INITDB_HOST'):
                connection_args['host'] = 'mongodb://%s' % str(self._config.get('MONGO_INITDB_HOST'))

            if self._config.get('MONGO_INITDB_ROOT_USERNAME'):
                if not self._config.get('MONGO_INITDB_HOST'):
                    raise ScrapeConditionsNotMetException('Specify mongo host if you use username and password auth')

                connection_args['host'] = connection_args.get('host')[:10] + '{usr}:{pwd}@'.format(
                    usr=self._config.get('MONGO_INITDB_ROOT_USERNAME'),
                    pwd=self._config.get('MONGO_INITDB_ROOT_PASSWORD')
                ) + connection_args.get('host')[10:]

            if self._config.get('MONGO_INITDB_PORT'):
                connection_args.update({'port': self._config.get('MONGO_INITDB_PORT')})
            self._mongo_client = MongoClient(**connection_args)

            # force connection on a request to check if server is online
            self._mongo_client.server_info()
            self._mongo_database = self._mongo_client['9GagMedia']
            self._mongo_gridfs = gridfs.GridFS(database=self._mongo_database)

        except RequestException:
            self.logger.error(
                'Forum %s is not reachable, can not start scraper' % ForumMediaScraper._SCRAPER_FORUM_HOME_PAGE_URL)
            raise ScrapeConditionsNotMetException
        except MongoServerSelectionTimeoutError as serverTimeout:
            self.logger.error('Could not create connection to mongoDB server: %s ' % serverTimeout)
            raise ScrapeConditionsNotMetException

    def run(self):
        try:
            # check scraper conditions
            self._check_scrape_conditions()

            self.logger.info('Configuring gecko selenium webdriver for python at {}..'.format(self._config.get('WEBDRIVER_EXECUTABLE_PATH')))
            web_driver_args = {
                "executable_path": self._config.get('WEBDRIVER_EXECUTABLE_PATH'),
                "log_path": './log/geckodriver.log',
                "firefox_binary": self._config.get('WEBDRIVER_BROWSER_EXECUTABLE_PATH')
            }

            # try to set up firefox driver for selenium and retrieve forum home page
            with _WebDriver(SeleniumWebdriver.Firefox(**web_driver_args)) as wd:
                wd.get(ForumMediaScraper._SCRAPER_FORUM_HOME_PAGE_URL)
                self.logger.info('Successfully loaded {}'.format(ForumMediaScraper._SCRAPER_FORUM_HOME_PAGE_URL))
                self.logger.info('###############FORUM MEDIA SCRAPER SCRAPE LOG###############')

                last_height = wd.execute_script("return document.body.scrollHeight")
                start_time = datetime.utcnow()

                # create run entry for scraper in mongo database
                result = self._mongo_database['Runs'].insert_one({
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
                    db=self._mongo_database,
                    fs=self._mongo_gridfs,
                    logger=self.logger
                )

                try:
                    while True:
                        # Scroll down to bottom to load all possible posts for this scrape cycle
                        wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")

                        # Wait to load page
                        time.sleep(ForumMediaScraper._SCRAPER_SCROLL_PAUSE_TIME)

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
                        if new_height == last_height or start_time + timedelta(seconds=int(self._config.get('SCRAPER_MAX_SCROLL_SECONDS'))) < datetime.utcnow():
                            break
                        last_height = new_height

                    # clean up when the MediaScraper has finished
                    media_processor.stop_reason = "Reached max scroll seconds limit"
                    del media_processor

                except AlreadyProcessedException:
                    del media_processor

        except SeleniumWebDriverException as driverException:
            self.logger.error('Could not create firefox driver using local geckodriver: {err}'.format(err=driverException.msg))
        except OSError as oserr:
            self.logger.error('Failed to find firefox executable: {err}'.format(err=oserr))
        except MongoServerSelectionTimeoutError as serverTimeout:
            self.logger.error('Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))


