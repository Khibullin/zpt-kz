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
        'zpt.kz,www.zpt.kz,.onrender.com,127.0.0.1,localhost',
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
    'django.contrib.humanize',

    'catalog',

    'core',
    'service_requests',
    'orders',
    'marketing',
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
                'orders.context_processors.cart_count',
                'marketing.context_processors.marketing_send_mode',
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
USE_THOUSAND_SEPARATOR = True
NUMBER_GROUPING = 3

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
SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_SAMESITE = 'None'
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

CSRF_TRUSTED_ORIGINS = [
    'https://zpt.kz',
    'https://www.zpt.kz',
    'https://zpt-kz-backend.onrender.com',
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Email (SMTP via environment variables)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 465
EMAIL_USE_TLS = False
EMAIL_USE_SSL = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'rkhaibullin@gmail.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
ORDER_ADMIN_EMAIL = os.getenv(
    'ORDER_ADMIN_EMAIL',
    EMAIL_HOST_USER,
)

SELLER_PIPELINE_EMAIL_ENABLED = os.getenv(
    'SELLER_PIPELINE_EMAIL_ENABLED',
    'False',
).lower() in ('true', '1', 'yes')
SELLER_PIPELINE_NOTIFICATION_EMAIL = os.getenv(
    'SELLER_PIPELINE_NOTIFICATION_EMAIL',
    ORDER_ADMIN_EMAIL or EMAIL_HOST_USER,
)

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
    "mp_request_v1",
)

WHATSAPP_TEMPLATE_LANG = os.getenv(
    "WHATSAPP_TEMPLATE_LANG",
    "ru"
)

WHATSAPP_BUYER_TEMPLATE_NAME = os.getenv(
    "WHATSAPP_BUYER_TEMPLATE_NAME",
    "zpt_buyer_request_receipt",
)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://zpt.kz")

BUYER_BROADCAST_MODE = (os.getenv("BUYER_BROADCAST_MODE", "OFF") or "OFF").strip().upper()
BUYER_BROADCAST_TEST_MAX_RECIPIENTS = int(
    os.getenv("BUYER_BROADCAST_TEST_MAX_RECIPIENTS", "5") or "5"
) if str(os.getenv("BUYER_BROADCAST_TEST_MAX_RECIPIENTS", "5") or "5").isdigit() else 5
if BUYER_BROADCAST_TEST_MAX_RECIPIENTS <= 0:
    BUYER_BROADCAST_TEST_MAX_RECIPIENTS = 5

MARKETING_WHATSAPP_SEND_MODE = (
    os.getenv("MARKETING_WHATSAPP_SEND_MODE", "OFF") or "OFF"
).strip().upper()

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN") or os.getenv("FACEBOOK_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID") or os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
INSTAGRAM_PUBLISH_MODE = (os.getenv("INSTAGRAM_PUBLISH_MODE", "OFF") or "OFF").strip().upper()

INSTAGRAM_BUSINESS_ACCOUNT_ID = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v20.0")

# Seller lead search (Brave Search API)
SELLER_SEARCH_PROVIDER = (os.getenv('SELLER_SEARCH_PROVIDER', 'brave') or 'brave').strip().lower()
BRAVE_SEARCH_API_KEY = (os.getenv('BRAVE_SEARCH_API_KEY', '') or '').strip()
SELLER_SEARCH_ENABLED = os.getenv('SELLER_SEARCH_ENABLED', 'False').lower() == 'true'

# Checkout / Kaspi (mock until bank credentials are issued)
ZPT_WAREHOUSE_ADDRESS = os.getenv(
    'ZPT_WAREHOUSE_ADDRESS',
    'г. Алматы, ул. Райымбека, 212б, корпус 3, бокс 5',
)
KASPI_MERCHANT_ID = os.getenv('KASPI_MERCHANT_ID', '')
KASPI_API_TOKEN = os.getenv('KASPI_API_TOKEN', '')
PHAETON_PRICE_MARKUP_PERCENT = int(os.getenv('PHAETON_PRICE_MARKUP_PERCENT', '15'))
ZPT_DEFAULT_WHATSAPP = os.getenv('ZPT_DEFAULT_WHATSAPP', '+77713607040')
ZPT_WAREHOUSE_CITY = os.getenv('ZPT_WAREHOUSE_CITY', 'Алматы')

MEDIA_URL = '/products/'
MEDIA_ROOT = BASE_DIR / 'products'

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'