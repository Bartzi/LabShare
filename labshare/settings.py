# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os

import ldap
from django_auth_ldap.config import LDAPSearch, GroupOfNamesType

ADMINS = ()
ALLOWED_HOSTS = []

ASGI_APPLICATION = "labshare.routing.application"

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',  # this is default
    'labshare.backends.authentication.ldap.LDAPBackend',
    'guardian.backends.ObjectPermissionBackend',
)

AUTH_LDAP_USER_ATTR_MAP = {
    "first_name": "cn",
    "last_name": "sn",
    "username": "uid",
    "email": "mail"
}
AUTH_LDAP_SERVER_URI = "ldaps://example.com"
AUTH_LDAP_USER_SEARCH = LDAPSearch("ou=People,dc=example,dc=com", ldap.SCOPE_SUBTREE, "(uid=%(user)s)")

AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
    "ou=Group,dc=example,dc=com", ldap.SCOPE_SUBTREE, "(objectClass=groupOfNames)"
)
AUTH_LDAP_GROUP_TYPE = GroupOfNamesType()

AUTH_LDAP_GROUP_MAP = {
    "cn=staff,ou=group,dc=example,dc=com": "Staff"
}
AUTH_LDAP_DEFAULT_GROUP_NAME = ""


CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],
        },
    },
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Database
# https://docs.djangoproject.com/en/1.8/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        'TEST': {
            'NAME': os.path.join(BASE_DIR, 'db-test.sqlite3'),
        }
    }
}

DATETIME_FORMAT = 'P d.n.'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True
DEFAULT_FROM_EMAIL = "admin@labshare.labshare"

EMAIL_BACKEND = 'labshare.backends.mail.open_smtp.OpenSMTPBackend'
EMAIL_HOST = "localhost"
EMAIL_PORT = "25"

HIJACK_USE_BOOTSTRAP = True

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'labshare',
    'bootstrap4',
    'guardian',
    'channels',
    'hijack',
    'compat',
    'rest_framework',
    'rest_framework.authtoken',
)

LANGUAGE_CODE = 'en-us'
LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/login"

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework.authentication.TokenAuthentication',
    )
}

ROOT_URLCONF = 'urls'

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 't&f&k54m3j^*vm8wgc2r&$aq47&dq-(b!!9tng))r2#zzr&un%'

SITE_ID = 1

STATICFILES_DIRS = (
    os.path.join(BASE_DIR, "static"),
)

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.8/howto/static-files/
STATIC_URL = '/static/'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')]
        ,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            'loaders': [
                'django.template.loaders.app_directories.Loader',
                'django.template.loaders.filesystem.Loader',
            ]
        },
    },
]


TIME_ZONE = 'CET'

USE_I18N = True
USE_L10N = False
USE_TZ = True

# Create a localsettings.py to override settings per machine or user, e.g. for
# development or different settings in deployments using multiple servers.
_LOCAL_SETTINGS_FILENAME = os.path.join(BASE_DIR, "localsettings.py")
if os.path.exists(_LOCAL_SETTINGS_FILENAME):
    exec(compile(open(_LOCAL_SETTINGS_FILENAME, "rb").read(), _LOCAL_SETTINGS_FILENAME, 'exec'))
del _LOCAL_SETTINGS_FILENAME
