"""Staging settings and globals."""
from os import environ
from .base import *

STATIC_URL = 'https://s3.amazonaws.com/media.beta.knightlab.us/cabrogger/'

# should these be in site.py?
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_HOST_PASSWORD = environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_HOST_USER = environ.get('EMAIL_HOST_USER', 'knightlab@northwestern.edu')
EMAIL_PORT = environ.get('EMAIL_PORT', 587)
EMAIL_SUBJECT_PREFIX = '[cabrogger] '
EMAIL_USE_TLS = True
SERVER_EMAIL = EMAIL_HOST_USER

DATABASES = {}
