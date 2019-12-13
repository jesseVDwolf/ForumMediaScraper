ForumMediaScraper package
=========================
Simple python3.6 application that scrapes the [9Gag hot](https://9gag.com/hot) forum's media content. It stores
this data into a local database. This app is part of a bigger system for detecting reposts on the 9Gag forum.

Getting started
---------------
The scraper requires a couple things to start. It requires a geckodriver executable to be 
in PATH or at a location specified in the config used to create the scraper instance. It needs
a firefox executable binary (for selenium). Lastly, it needs a mongo database to save its data too. 
Settings for connecting to mongo can be set in the config.

the config:

| Option name | Data type | Description | Default |
| ----------- | --------- | ----------- | ------- |
| MONGO_INITDB_ROOT_USERNAME| str | Username used to connect to the mongo database server | None |
| MONGO_INITDB_ROOT_PASSWORD| str | Password used to connect to the mongo database server | None |
| MONGO_INITDB_HOST| str | Hostname (i.e. localhost) | None |
| MONGO_INITDB_PORT| int | Port the mongo database server is listening on | 27017 |
| SCRAPER_MAX_SCROLL_SECONDS| int | How many seconds will the scraper keep scanning the 9gag hot page | 60 |
| SCRAPER_CREATE_SERVICE_LOG| bool | True if you want the scraper to push its logs to a file instead of stdout | False |
| SCRAPER_HEADLESS_MODE| bool | Used to set MOZ_HEADLESS which if set to true will run firefox headless | True |
| WEBDRIVER_EXECUTABLE_PATH| str | Directory path to the geckodriver executable. | ./geckodriver |
| WEBDRIVER_BROWSER_EXECUTABLE_PATH| str | Directory path to the firefox executable | None |

Setup
----------
Use git clone to download the repository:

```bash
git clone https://github.com/jesseVDwolf/ForumMediaScraper.git
```

Install the package using pip. Make sure you're in the same directory as the setup.py file:
```bash
pip install .
```

Start using the ForumMediaScraper:
```python
from ForumMediaScraper import ForumMediaScraper

config = {
    'MONGO_INITDB_ROOT_USERNAME': 'admin',
    'MONGO_INITDB_ROOT_PASSWORD': 'Noobmaster69',
    'MONGO_INITDB_HOST': '127.0.0.1',
    'WEBDRIVER_EXECUTABLE_PATH': './geckodriver.exe',
    'SCRAPER_HEADLESS_MODE': True,
    'WEBDRIVER_BROWSER_EXECUTABLE_PATH': 'C:\\Program Files\\Mozilla Firefox\\firefox.exe'
}

scraper = ForumMediaScraper(config=config)
scraper.run()
```

