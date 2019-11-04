ForumMediaScraper package
=========================
Simple python3.6 application that scrapes the [9Gag hot](https://9gag.com/hot) forum's media content. It stores
this data into a local database. This app is part of a bigger system for detecting reposts on the 9Gag forum.

Getting started
---------------
The scraper requires a couple of things to work. Firstly, some environmental variables have to be set:

* MONGO_INITDB_ROOT_USERNAME 
* MONGO_INITDB_ROOT_PASSWORD
* MOZ_HEADLESS
* MAX_SCROLL_SECONDS 

The MONGO_INITDB variables are used to connect to a local MongoDB server (make sure the server is running
reachable over localhost port 27017).Creation of the database, collections etcetera is done by the scraper. 
The MOX_HEADLESS variable is optional and specifies if you want open firefox in headless mode, meaning that 
no browser GUI opens. MAX_SCROLL_SECONDS defines for how long the scraper will keep scrolling down the page 
to load new posts and scrape them.

To then start the scraper all you have to do is import the start_scraper function from the ForumMediaScraper package
and run this. If something was not set up correctly it will tell you directly.

Prerequisites
-------------
