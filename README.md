# LabShare [![Build Status](https://travis-ci.org/Bartzi/LabShare.svg?branch=master)](https://travis-ci.org/Bartzi/LabShare) [![Coverage Status](https://coveralls.io/repos/Bartzi/LabShare/badge.svg?branch=master&service=github)](https://coveralls.io/github/Bartzi/LabShare?branch=master)

Django Tool that helps everyone to get their fair share of GPU time.

## Installation

1. clone repository
2. make sure that `OpenLDAP` and `SASL` are installed  (under Ubuntu they can be installed using this command: `apt-get install libldap2-dev libsasl2-dev`)
3. install requirements with `pip install -r requirements.txt` (make sure to use python 3 (>=3.6)!) 
4. start a redis server instance (you can use a docker container and start it with the following command: `docker run -p 6379:6379 -d redis`)
5. create database by running `python manage.py migrate`
6. Install [Yarn](https://yarnpkg.com/en/docs/install)
7. go into folder `static` and run `yarn` or `yarn install` to install all front end libraries.
8. run the test server with `python manage.py runserver` (in the root directory)

## Usage

1. create superuser by running `python manage.py createsuperuser`
2. If you want to have more users, you can create them using the Admin WebInterface (`/admin`).
3. create a new `Device` in the django admin for every device you want to monitor
2. deploy the `device_query` script on every machine that has a GPU that shall be monitored
3. copy the `example.ini` file and rename it to `config.ini`
    * change `device_name` to the name of the device that was created in the admin interface
    * change the `server_url` to the address where the Django server is running
    * on the Django machine, execute the commands `python manage.py tokens` or `python manage.py token [device_name]` to
    get the authentication token of the registered device and paste it in the config file
4. run the `device_query` script 

## Configuration

In order to make it possible for users to see the devices and their gpus you need to give each user the permission to do so!
You can do this in one of the following ways:

1. Add the `use_device` permission to a group of your choice (for instance the default Staff group) and add users to this group. this global permission allows each user in that group to use all GPUs in LabShare. This allows you to easily provide the necessary permission to each user.
2. For fine-grained control you can control who can use which device, by adding the `use_device` permission to each user or a group in the permission admin of each device.
