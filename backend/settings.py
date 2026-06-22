import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

_DEV_SECRET_KEY = 'django-insecure-dev-only-change-me'
_BUILD_COMMANDS = {'collectstatic', 'migrate', 'check', 'makemigrations'}
_is_build_command = (
    len(sys.argv) > 1 and sys.argv[1] in _BUILD_COMMANDS
)

SECRET_KEY = os.getenv('SECRET_KEY', '').strip()

if not SECRET_KEY:
    if DEBUG or _is_build_command:
        SECRET_KEY = _DEV_SECRET_KEY
    else:
        raise ImproperlyConfigured(
            'SECRET_KEY environment variable must be set when DEBUG is False.'
        )
elif SECRET_KEY == _DEV_SECRET_KEY and not DEBUG and not _is_build_command:
    raise ImproperlyConfigured(
        'SECRET_KEY must not use the development default in production.'
    )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        'ALLOWED_HOSTS',
        '127.0.0.1,localhost,.onrender.com,zpt.kz,www.zpt.kz'
    ).split(',')
    if host.strip()
]

INSTALLED_APPS = [
    'corsheaders',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'catalog',

    'core',
    'service_requests',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        },
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0
WHITENOISE_SKIP_COMPRESS_EXTENSIONS = (
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico', 'woff', 'woff2',
)

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    'https://zpt.kz',
    'https://www.zpt.kz',
    'https://zpt-kz-backend.onrender.com',
]

SESSION_COOKIE_SAMESITE = 'None'
SESSION_COOKIE_SECURE = True

CSRF_COOKIE_SAMESITE = 'None'
CSRF_COOKIE_SECURE = True

CSRF_TRUSTED_ORIGINS = [
    'https://zpt.kz',
    'https://www.zpt.kz',
    'https://zpt-kz.onrender.com',
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# WhatsApp / Meta API

WHATSAPP_TOKEN = (
    os.getenv("WHATSAPP_TOKEN")
    or os.getenv("WHATSAPP_ACCESS_TOKEN")
)

META_PHONE_NUMBER_ID = (
    os.getenv("META_PHONE_NUMBER_ID")
    or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
)

WHATSAPP_TEMPLATE_NAME = os.getenv(
    "WHATSAPP_TEMPLATE_NAME",
    "zpt_request_notification"
)

WHATSAPP_TEMPLATE_LANG = os.getenv(
    "WHATSAPP_TEMPLATE_LANG",
    "ru"
)

MEDIA_URL = '/products/'
MEDIA_ROOT = BASE_DIR / 'products'

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'