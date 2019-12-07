from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='ForumMediaScraper',
    version='0.1.0',
    description='Simple web scraper application for https://9gag.com/hot',
    long_description=readme,
    author='Jesse van der Wolf',
    author_email='j3ss3hop@yahoo.nl',
    url='https://github.com/kennethreitz/samplemod',
    license=license,
    packages=find_packages(),
    install_requires=requirements
)