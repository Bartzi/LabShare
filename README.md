# LabShare [![Build Status](https://travis-ci.org/Bartzi/LabShare.svg?branch=master)](https://travis-ci.org/Bartzi/LabShare) [![Coverage Status](https://coveralls.io/repos/Bartzi/LabShare/badge.svg?branch=master&service=github)](https://coveralls.io/github/Bartzi/LabShare?branch=master)

Django Tool that helps everyone to get their fair share of GPU time.

## Installation

1. clone repository
2. install requirements with `pip install -r requirements.txt` (make sure to use python 3!)
3. create database by running `python manage.py migrate`
4. run the test server with `python manage.py runserver`
5. 

## Usage

1. create superuser by running `python manage.py createsuperuser`
2. deploy the `device_query` script on every machine that has a GPU that shall be monitored
3. create a new `Device` in the django admin for every device you want to monitor
4. after you've created the devices and deployed and started the `device_query` scripts you should run `python manage.py update` which will fill your database with information on the GPUs that each device has.
5. If you want to have updates regularly you should create a cron job that runs the update job every now and then.
