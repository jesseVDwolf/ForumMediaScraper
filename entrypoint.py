from ForumMediaScraper.Scraper import ScraperConfig, SeleniumScraper

config = ScraperConfig()
scraper = SeleniumScraper(config)
scraper.run()
