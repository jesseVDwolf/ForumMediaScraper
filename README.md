ForumMediaScraper package
=========================
Simple python3.6 application that scrapes the [9Gag hot](https://9gag.com/hot) forum's media content. It stores
this data into a local database. This app is part of a bigger system for detecting reposts on the 9Gag forum.

Getting started
---------------
The scraper requires a couple of things to work. Firstly, some environmental variables have to be set:

```bash
#required
export MONGO_INITDB_ROOT_USERNAME = username
export MONGO_INITDB_ROOT_PASSWORD = pwd

#optional
export MOZ_HEADLESS = 1
export MAX_SCROLL_SECONDS = 60
export LOGGING_TYPE = file
```

The MONGO_INITDB variables are used to connect to a local MongoDB server (make sure the server is running
reachable over localhost port 27017). Creation of the database, collections etcetera is done by the scraper.
The MOZ_HEADLESS variable is optional and specifies if you want open firefox in headless mode, meaning that 
no browser GUI opens. MAX_SCROLL_SECONDS defines for how long the scraper will keep scrolling down the page 
to load new posts and scrape them. LOGGING_TYPE specifies if you want the service its logs to be pushed to 
stdout (a.k.a the terminal) or to a log file at ./ForumMediaScraper/service.log

Secondly, use pip to install the packages specified in the requirements.txt file using the provided setup.py:\
*pip install .*

When this is all done. You can start the scraper by importing the ForumMediaScraper class from the ForumMediaScraper
package and run the *start_scraper* function:
```python
from ForumMediaScraper import ForumMediaScraper

media_scraper = ForumMediaScraper()
media_scraper.start_scraper()
```