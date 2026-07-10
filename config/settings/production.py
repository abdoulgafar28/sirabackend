# config/settings/production.py

import os
import dj_database_url
from .base import *   # hérite de base.py

SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = False
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',')
ALLOWED_HOSTS = [
    'sirabackend-pc8j.onrender.com', 
    'localhost', 
    '127.0.0.1'
]

# Base de données via DATABASE_URL (Render)
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', ''),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

CORS_ALLOWED_ORIGINS = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:8000'
).split(',')

# Fichiers statiques
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'