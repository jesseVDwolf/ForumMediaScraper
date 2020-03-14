FROM python:3.6-buster

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y git && \
    git clone https://github.com/jesseVDwolf/ForumMediaScraper.git scraper&& \
    (cd scraper; /bin/bash -c "pip install .")

