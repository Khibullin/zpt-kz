import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from backend.media_paths import resolve_media_root

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
    'repricer',

    'core',
    'service_requests',
    'orders',
    'payments.apps.PaymentsConfig',
    'marketing',
    'control_panel.apps.ControlPanelConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'core.seo_middleware.SeoRobotsHeaderMiddleware',
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
                'core.seo.seo_context',
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
    "zpt_request_notification_v2",
)

WHATSAPP_TEMPLATE_LANG = os.getenv(
    "WHATSAPP_TEMPLATE_LANG",
    "ru"
)

WHATSAPP_BUYER_TEMPLATE_NAME = os.getenv(
    "WHATSAPP_BUYER_TEMPLATE_NAME",
    "zpt_buyer_request_receipt",
)

WHATSAPP_SERVICE_TEMPLATE_NAME = os.getenv(
    "WHATSAPP_SERVICE_TEMPLATE_NAME",
    "zpt_request_notification",
)

WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv(
    'WHATSAPP_WEBHOOK_VERIFY_TOKEN',
    '',
).strip()
META_APP_SECRET = os.getenv('META_APP_SECRET', '').strip()

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://zpt.kz")
SEO_PRODUCT_SITEMAP_ENABLED = os.getenv('SEO_PRODUCT_SITEMAP_ENABLED', 'True').lower() in ('true', '1', 'yes')

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

# Seller product assistant (OpenAI Responses API + web_search)
OPENAI_API_KEY = (os.getenv('OPENAI_API_KEY', '') or '').strip()
PRODUCT_AI_MODEL = (
    os.getenv('PRODUCT_AI_MODEL', 'gpt-5.6-luna') or 'gpt-5.6-luna'
).strip() or 'gpt-5.6-luna'

# Platform help (text + voice). No web_search; controlled ZPT.KZ context only.
HELP_AI_MODEL = (
    os.getenv('HELP_AI_MODEL', 'gpt-5.6-luna') or 'gpt-5.6-luna'
).strip()
HELP_TRANSCRIBE_MODEL = (
    os.getenv('HELP_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
    or 'gpt-4o-mini-transcribe'
).strip()
HELP_ASK_MAX_PER_HOUR = int(os.getenv('HELP_ASK_MAX_PER_HOUR', '30') or '30')
HELP_TRANSCRIBE_MAX_PER_HOUR = int(os.getenv('HELP_TRANSCRIBE_MAX_PER_HOUR', '12') or '12')
HELP_RATE_LIMIT_WINDOW = int(os.getenv('HELP_RATE_LIMIT_WINDOW', '3600') or '3600')
HELP_EMAIL_ENABLED = os.getenv(
    'HELP_EMAIL_ENABLED',
    'True',
).lower() in ('true', '1', 'yes')
HELP_NOTIFICATION_EMAIL = os.getenv(
    'HELP_NOTIFICATION_EMAIL',
    ORDER_ADMIN_EMAIL or EMAIL_HOST_USER,
)

# Robokassa Kazakhstan. Stage 1: test payments only. Live is never honoured.
def _env_flag(name, default=False):
    raw = os.getenv(name, 'true' if default else 'false')
    return str(raw or '').strip().lower() in ('true', '1', 'yes')


ROBOKASSA_ENABLED = _env_flag('ROBOKASSA_ENABLED', False)
ROBOKASSA_TEST_ENABLED = _env_flag('ROBOKASSA_TEST_ENABLED', False)
# Explicit stage-1 deny. A true environment value must not enable live.
ROBOKASSA_LIVE_ENABLED = False
ROBOKASSA_MERCHANT_LOGIN = (
    os.getenv('ROBOKASSA_MERCHANT_LOGIN', 'zptkz') or 'zptkz'
).strip() or 'zptkz'
ROBOKASSA_HASH_ALGO_TEST = (
    os.getenv('ROBOKASSA_HASH_ALGO_TEST', 'sha256') or 'sha256'
).strip().lower() or 'sha256'
ROBOKASSA_PASS1_TEST = os.getenv('ROBOKASSA_PASS1_TEST', '').strip()
ROBOKASSA_PASS2_TEST = os.getenv('ROBOKASSA_PASS2_TEST', '').strip()
ROBOKASSA_OWN_SELLER_PROFILE_ID = os.getenv(
    'ROBOKASSA_OWN_SELLER_PROFILE_ID',
    '',
).strip()

# Checkout / Kaspi (mock until bank credentials are issued)
ZPT_WAREHOUSE_ADDRESS = os.getenv(
    'ZPT_WAREHOUSE_ADDRESS',
    'г. Алматы, ул. Мурат, 94А',
)
KASPI_MERCHANT_ID = os.getenv('KASPI_MERCHANT_ID', '')
KASPI_API_TOKEN = os.getenv('KASPI_API_TOKEN', '')
# Own Kaspi shop identity for competitor display. Keep production values out of git.
KASPI_OWN_MERCHANT_IDS = os.getenv('KASPI_OWN_MERCHANT_IDS', '')
KASPI_OWN_MERCHANT_NAMES = os.getenv('KASPI_OWN_MERCHANT_NAMES', '')
try:
    KASPI_COMPETITOR_FRESH_MINUTES = int(
        os.getenv('KASPI_COMPETITOR_FRESH_MINUTES', '180') or '180'
    )
except ValueError:
    KASPI_COMPETITOR_FRESH_MINUTES = 180
if KASPI_COMPETITOR_FRESH_MINUTES <= 0:
    KASPI_COMPETITOR_FRESH_MINUTES = 180
try:
    KASPI_REPRICER_UNDERCUT_AMOUNT = int(
        os.getenv('KASPI_REPRICER_UNDERCUT_AMOUNT', '300') or '300'
    )
except ValueError:
    KASPI_REPRICER_UNDERCUT_AMOUNT = 300
if KASPI_REPRICER_UNDERCUT_AMOUNT < 0:
    KASPI_REPRICER_UNDERCUT_AMOUNT = 300
# Machine-to-machine token for the office Kaspi collector. Keep the real value out of git.
KASPI_COMPETITOR_COLLECTOR_TOKEN = os.getenv('KASPI_COMPETITOR_COLLECTOR_TOKEN', '')
ZPT_KASPI_COLLECTOR_BASE_URL = os.getenv('ZPT_KASPI_COLLECTOR_BASE_URL', '').strip()
PHAETON_PRICE_MARKUP_PERCENT = int(os.getenv('PHAETON_PRICE_MARKUP_PERCENT', '15'))
ZPT_DEFAULT_WHATSAPP = os.getenv('ZPT_DEFAULT_WHATSAPP', '+77713607040')
ZPT_WAREHOUSE_CITY = os.getenv('ZPT_WAREHOUSE_CITY', 'Алматы')


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Homepage short-form public limits. Shared via DB (not LocMem).
# HOME_PARTS_NUM_PROXIES=1 matches Render: take the right-hand X-Forwarded-For hop.
HOME_PARTS_MAX_PER_HOUR = _env_int('HOME_PARTS_MAX_PER_HOUR', 8)
HOME_PARTS_MAX_PER_PHONE_HOUR = _env_int('HOME_PARTS_MAX_PER_PHONE_HOUR', 5)
HOME_PARTS_RATE_LIMIT_WINDOW = _env_int('HOME_PARTS_RATE_LIMIT_WINDOW', 3600)
HOME_PARTS_NUM_PROXIES = _env_int('HOME_PARTS_NUM_PROXIES', 1)

MEDIA_URL = '/products/'
MEDIA_ROOT = resolve_media_root(os.getenv('MEDIA_ROOT'), BASE_DIR)

# Catalog import archives live ON the persistent MEDIA_ROOT volume,
# in a dedicated service folder that is not ProductImage upload storage.
_import_archive_raw = (os.getenv('IMPORT_ARCHIVE_ROOT') or '').strip()
IMPORT_ARCHIVE_ROOT = (
    Path(_import_archive_raw)
    if _import_archive_raw
    else Path(MEDIA_ROOT) / '_catalog_imports'
)
IMPORT_ARCHIVE_KEEP_SUCCESSFUL = int(os.getenv('IMPORT_ARCHIVE_KEEP_SUCCESSFUL', '20'))
CATALOG_IMPORT_SHRINK_RATIO = float(os.getenv('CATALOG_IMPORT_SHRINK_RATIO', '0.20'))
CATALOG_IMPORT_SHRINK_ABS = int(os.getenv('CATALOG_IMPORT_SHRINK_ABS', '10'))

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'