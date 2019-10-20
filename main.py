import os
import time
from datetime import datetime, timedelta

from selenium import webdriver
from bs4 import BeautifulSoup


HOME_PAGE_URL = "https://9gag.com/hot"
GECKO_DRIVER_PATH = 'bin\\geckodriver.exe'
SCROLL_PAUSE_TIME = 0.5
MAX_SCROLL_SECONDS = 3


def main():
    driver = webdriver.Firefox(executable_path=r'{}\{}'.format(os.getcwd(), GECKO_DRIVER_PATH))
    driver.get(HOME_PAGE_URL)

    last_height = driver.execute_script("return document.body.scrollheight")
    start_time = datetime.utcnow()

    while True:
        # Scroll down to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        post_containers = soup.find_all('div', {'class': 'post-container'})
        print(post_containers)
        print(len(post_containers))

        # Wait to load page
        time.sleep(SCROLL_PAUSE_TIME)

        # Calculate new scroll height and compare with last scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height or start_time + timedelta(seconds=MAX_SCROLL_SECONDS) < datetime.utcnow():
            break
        last_height = new_height

    driver.close()


if __name__ == '__main__':
    main()
