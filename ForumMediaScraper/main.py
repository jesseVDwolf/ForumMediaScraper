import os
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
from pymongo.errors import ServerSelectionTimeoutError as MongoServerSelectionTimeoutError

from ForumMediaScraper.MediaProcessor import MediaProcessor, AlreadyProcessedException

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S'
)

MONGO_INITDB_ROOT_USERNAME = os.getenv('MONGO_INITDB_ROOT_USERNAME')
MONGO_INITDB_ROOT_PASSWORD = os.getenv('MONGO_INITDB_ROOT_PASSWORD')
MAX_SERVER_SELECTION_DELAY = 1

FORUM_HOME_PAGE_URL = "https://9gag.com/hot"
GECKO_DRIVER_PATH = 'ForumMediaScraper\\bin\\geckodriver.exe'

SCROLL_PAUSE_TIME = 0.5
MAX_SCROLL_SECONDS = os.getenv('MAX_SCROLL_SECONDS')


class WebDriver:
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


def main():
    # check if forum is online and accessable
    try:
        response = requests.get(FORUM_HOME_PAGE_URL)
        response.raise_for_status()
    except RequestException:
        logging.error('Forum {} is not reachable, can not start scraper'.format(FORUM_HOME_PAGE_URL))
        sys.exit(1)

    # check if environment is set up correctly
    if not MONGO_INITDB_ROOT_PASSWORD or not MONGO_INITDB_ROOT_USERNAME or not hasattr(MAX_SCROLL_SECONDS, 'isdigit'):
        logging.error('Environment not setup correctly, are all environment variables set up?')
        sys.exit(1)

    try:
        #  create mongo client to interact with local mongoDB instance
        mongo_client = MongoClient('mongodb://{usr}:{pwd}@127.0.0.1'.format(
            usr=urllib.parse.quote_plus(MONGO_INITDB_ROOT_USERNAME),
            pwd=urllib.parse.quote_plus(MONGO_INITDB_ROOT_PASSWORD)),
            serverSelectionTimeoutMS=MAX_SERVER_SELECTION_DELAY
        )

        # force connection on a request to check if server is online
        mongo_client.server_info()
        database = mongo_client['9GagMedia']
        filesystem = gridfs.GridFS(database=database)

        # try to set up firefox driver for selenium and retrieve forum home page
        with WebDriver(SeleniumWebdriver.Firefox(executable_path=r'{}\{}'.format(os.getcwd(), GECKO_DRIVER_PATH))) as wd:
            wd.get(FORUM_HOME_PAGE_URL)

            last_height = wd.execute_script("return document.body.scrollHeight")
            start_time = datetime.utcnow()

            # create run entry for scraper in mongo database
            result = database['Runs'].insert_one({
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
            media_processor = MediaProcessor(scraper_run_id=result.inserted_id, db=database, fs=filesystem)

            try:
                while True:
                    # Scroll down to bottom to load all possible posts for this scrape cycle
                    wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")

                    # Wait to load page
                    time.sleep(SCROLL_PAUSE_TIME)

                    # build regex search for stream using last know stream id
                    last_stream_id = stream_tracker[-1] if len(stream_tracker) > 0 else '0'
                    regex = media_processor.create_stream_list_regex(stream_id=last_stream_id)

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
                    if new_height == last_height or start_time + timedelta(seconds=int(MAX_SCROLL_SECONDS)) < datetime.utcnow():
                        break
                    last_height = new_height

                # clean up when the MediaScraper has finished
                media_processor.stop_reason = "Processed all requested articles"
                del media_processor

            except AlreadyProcessedException:
                del media_processor
                sys.exit(1)

    except SeleniumWebDriverException as driverException:
        logging.error('Could not create firefox driver using local geckodriver: {err}'.format(err=driverException.msg))
        sys.exit(1)
    except MongoServerSelectionTimeoutError as serverTimeout:
        logging.error('Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))
        sys.exit(1)


def start_scraper():
    main()


if __name__ == '__main__':
    main()
