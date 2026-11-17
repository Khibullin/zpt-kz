"""Single explicitly requested Kaspi access check for Omega.

Reads one already linked public Omega card and one official categories endpoint.
No product matching, price updates, image fetch, retries, or Kaspi writes.
Does not log URLs, articles, credentials, response bodies, or request headers.
"""
import logging

import requests
from django.core.cache import cache

from .models import KaspiReviewTask

log = logging.getLogger(__name__)
CHECK_KEY = 'procurement:omega-public-api-diagnostic:2026-11-17:v1'


def check_omega_kaspi_once():
    # Cross-worker lease guards against accidental repeated requests.
    if not cache.add(CHECK_KEY, 'checking', timeout=7 * 86400):
        return 'already_attempted'
    task = (KaspiReviewTask.objects.filter(
        offer__supplier__name='Omega',
        status='blocked',
    ).exclude(offer__kaspi_url='').select_related('offer').order_by('pk').first())
    if task is None:
        log.warning('Omega Kaspi diagnostic: no blocked URL available; request skipped')
        return 'no_sample'
    from .kaspi_identity_worker import read_public_product_page, KaspiAccessDenied
    from .kaspi_catalog_api import CatalogClient, CatalogError, connected
    # Report actual HTTP code, never print a private URL or token.
    original = task.offer.kaspi_url
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(original)
        if parsed.scheme != 'https' or parsed.hostname != 'kaspi.kz' or not parsed.path.startswith('/shop/p/'):
            log.warning('Omega Kaspi public GET: invalid saved public link')
            return 'invalid'
        with requests.Session() as session:
            with session.get(original, stream=True, allow_redirects=False,
                             timeout=(4, 10), headers={
                                 'Accept': 'text/html',
                                 'User-Agent': 'ZPT-KZ-Procurement/1.0 (+https://zpt.kz)',
                             }) as response:
                status = response.status_code
                log.warning('Omega Kaspi public GET: HTTP %s; retry_after_header=%s',
                            status, 'present' if response.headers.get('Retry-After') else 'absent')
    except requests.Timeout:
        status = None
        log.warning('Omega Kaspi public GET: request timed out')
    except requests.RequestException as exc:
        status = None
        log.warning('Omega Kaspi public GET: network error %s', type(exc).__name__)
    # Independent official seller-catalogue API: cannot supply competitor prices.
    if connected():
        try:
            result = CatalogClient().categories()
            log.warning('Omega Kaspi official categories GET: success; payload_type=%s',
                        type(result).__name__)
        except CatalogError as exc:
            log.warning('Omega Kaspi official categories GET: %s', str(exc))
    else:
        log.warning('Omega Kaspi official categories GET: no token configured')
    return status
