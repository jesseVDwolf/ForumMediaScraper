import os
import re
import sys
import time
import urllib.parse
from requests import get
from requests.exceptions import RequestException
from datetime import datetime, timedelta

from selenium import webdriver as SeleniumWebdriver
from selenium.common.exceptions import WebDriverException as SeleniumWebDriverException

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError as MongoServerSelectionTimeoutError

from bs4 import BeautifulSoup

import logging
#logging.basicConfig(filename='service.log', level=logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)

MONGO_INITDB_ROOT_USERNAME = os.getenv('MONGO_INITDB_ROOT_USERNAME')
MONGO_INITDB_ROOT_PASSWORD = os.getenv('MONGO_INITDB_ROOT_PASSWORD')
MAX_SERVER_SELECTION_DELAY = 1

FORUM_HOME_PAGE_URL = "https://9gag.com/hot"
GECKO_DRIVER_PATH = 'bin\\geckodriver.exe'

SCROLL_PAUSE_TIME = 0.5
MAX_SCROLL_SECONDS = 2


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


def main():
    # check if forum is online and accessable
    try:
        response = get(FORUM_HOME_PAGE_URL)
        response.raise_for_status()
    except RequestException:
        logging.error('Forum {forum} is not reachable, can not start the scraper'.format(forum=FORUM_HOME_PAGE_URL))
        sys.exit(1)

    # check if environment is set up correctly
    if not MONGO_INITDB_ROOT_PASSWORD or not MONGO_INITDB_ROOT_USERNAME:
        logging.error('Environment not setup correctly, have you set the MONGO_INITDB variables?')
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

        # try to set up firefox driver for selenium and retrieve forum home page
        firefox_driver = SeleniumWebdriver.Firefox(executable_path=r'{}\{}'.format(os.getcwd(), GECKO_DRIVER_PATH))
        firefox_driver.get(FORUM_HOME_PAGE_URL)

        last_height = firefox_driver.execute_script("return document.body.scrollHeight")
        start_time = datetime.utcnow()

        """
        The 9gag forums are build up using javascript list streams <div id="streams-x"> with a max of
        5 <article id="jsid-post-xxxxxxx"> posts per list stream. Using the list stream ids you can start 
        the scraping where the scraper last ended since list stream items are added while the page scrolls down.
        """
        stream_tracker = []

        while True:
            # Scroll down to bottom to load all possible posts for this scrape cycle
            firefox_driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

            # Wait to load page
            time.sleep(SCROLL_PAUSE_TIME)

            # build regex search for stream using last know stream id
            last_stream_id = stream_tracker[-1] if len(stream_tracker) > 0 else '0'
            regex = create_stream_list_regex(stream_id=last_stream_id)

            # create BeautifullSoup object for easier access to html data
            soup = BeautifulSoup(firefox_driver.page_source, 'html.parser')
            for list_stream in soup.find_all('div', {'id': regex}):

                # add id to stream tracker
                stream_id = str(list_stream['id'])
                stream_tracker.append(stream_id[stream_id.find('-') + 1:len(stream_id)])

                for article in list_stream.find_all('article'):
                    print(article.get('id'))

            # Calculate new scroll height and compare with last scroll height
            new_height = firefox_driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height or start_time + timedelta(seconds=MAX_SCROLL_SECONDS) < datetime.utcnow():
                break
            last_height = new_height

        firefox_driver.close()

    except SeleniumWebDriverException as driverException:
        logging.error('Could not create firefox driver using local geckodriver: {err}'.format(err=driverException.msg))
        sys.exit(1)
    except MongoServerSelectionTimeoutError as serverTimeout:
        logging.error('Could not create connection to mongoDB server: {err}'.format(err=serverTimeout))
        sys.exit(1)


if __name__ == '__main__':
    main()
