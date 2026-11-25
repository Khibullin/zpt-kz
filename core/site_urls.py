"""Keep office and integration links on the closed origin after shop cutover."""
from django.conf import settings


def office_base_url():
    if getattr(settings, 'DOMAIN_BOUNDARY_ENABLED', False):
        return 'https://zpt.kz'
    return str(getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://zpt.kz').rstrip('/')


def office_url(path):
    return office_base_url() + '/' + str(path).lstrip('/')
