"""Conservative read-only identity verification for already bound Kaspi URLs.

Only exact Kaspi product metadata can authorize an automatic match. No search
results, numeric article guesses, image matching, or merchant APIs are used.
Failed identity checks never renew product prices or alter stock.
"""
import logging
from urllib.parse import urlsplit

import requests
from django.db import transaction
from django.utils import timezone

from .matching import compare_identity
from .models import KaspiReviewTask, Offer

logger = logging.getLogger(__name__)
MAX_PAGE_BYTES = 1024 * 1024


def read_public_product_page(url, *, session=None):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'kaspi.kz'
            or not parsed.path.startswith('/shop/p/')):
        raise ValueError('Не подтверждена публичная ссылка на товар Kaspi.')
    session = session or requests.Session()
    with session.get(
        url, allow_redirects=False, stream=True, timeout=(4, 10),
        headers={'Accept': 'text/html', 'User-Agent': 'ZPT-KZ-Procurement/1.0 (+https://zpt.kz)'},
    ) as response:
        if response.status_code in (401, 403, 429, 503):
            raise KaspiAccessDenied('Kaspi ограничил доступ к публичным карточкам.')
        if response.status_code != 200:
            raise ValueError('Карточка Kaspi недоступна: HTTP ' + str(response.status_code))
        page = bytearray()
        for chunk in response.iter_content(16384):
            page.extend(chunk)
            if len(page) > MAX_PAGE_BYTES:
                raise ValueError('Ответ Kaspi слишком большой.')
        return page.decode('utf-8', errors='replace')


class KaspiAccessDenied(ValueError):
    pass


def validate_public_identity(offer, page):
    # Read-only structured metadata; missing fields fail closed.
    from .kaspi_enrichment import exact_products
    for product in exact_products(page, offer.brand, offer.article):
        title = product.get('name', '')
        if isinstance(title, str):
            matched, _reason = compare_identity(
                offer, title, offer.article, offer.brand)
            if matched:
                return {
                    'confirmed': True,
                    'source_url': offer.kaspi_url,
                    'title': title,
                    'article': offer.article,
                    'brand': offer.brand,
                    'source': 'kaspi_public_product_jsonld',
                }
    return None


def verify_bound_card(task_id, *, reader=read_public_product_page):
    task = KaspiReviewTask.objects.select_related('offer', 'batch').get(pk=task_id)
    offer = task.offer
    if task.status != 'pending' or offer.match_verified or not offer.kaspi_url:
        return 'skipped'
    stamp = timezone.now()
    if not KaspiReviewTask.objects.filter(pk=task.pk, status='pending').update(
            status='checking', updated_at=stamp):
        return 'skipped'
    original = (offer.kaspi_url, offer.brand, offer.article, offer.name)
    try:
        evidence = validate_public_identity(offer, reader(offer.kaspi_url))
    except KaspiAccessDenied:
        # Do not repeatedly probe the source after an access denial.
        KaspiReviewTask.objects.filter(
            batch_id=task.batch_id, status='pending',
            offer__match_verified=False,
        ).exclude(offer__kaspi_url='').update(
            status='blocked',
            reason='Источник Kaspi ограничил доступ. Автоматическая проверка остановлена.')
        KaspiReviewTask.objects.filter(pk=task.pk, status='checking', updated_at=stamp).update(
            status='blocked', reason='Источник Kaspi ограничил доступ. Проверка остановлена.')
        return 'source_blocked'
    except (requests.RequestException, ValueError) as error:
        logger.info('Kaspi candidate could not be checked: %s', type(error).__name__)
        KaspiReviewTask.objects.filter(pk=task.pk, status='checking', updated_at=stamp).update(
            status='blocked', reason='Публичная карточка Kaspi недоступна или неполна; требуется проверка.')
        return 'unverified'
    if not evidence:
        KaspiReviewTask.objects.filter(pk=task.pk, status='checking', updated_at=stamp).update(
            status='blocked', reason='Kaspi не подтвердил точный артикул, бренд, название и комплектность.')
        return 'unverified'
    with transaction.atomic():
        current_task = KaspiReviewTask.objects.select_for_update().get(pk=task.pk)
        current = Offer.objects.select_for_update().get(pk=offer.pk)
        if current_task.status != 'checking' or current_task.updated_at != stamp:
            return 'skipped'
        if (current.kaspi_url, current.brand, current.article, current.name) != original:
            current_task.status = 'pending'
            current_task.reason = 'Карточка или товар изменились; требуется повторная проверка.'
            current_task.save(update_fields=['status', 'reason', 'updated_at'])
            return 'skipped'
        current.match_verified = True
        current.characteristics = {
            **(current.characteristics or {}),
            'kaspi_identity': {**evidence, 'checked_on': timezone.localdate().isoformat()},
        }
        current.save(update_fields=['match_verified', 'characteristics'])
        current_task.status = 'pending'
        current_task.reason = 'Совпадение подтверждено; ожидает обновления цены Kaspi.'
        current_task.save(update_fields=['status', 'reason', 'updated_at'])
    return 'verified'


def process_pending_bound_cards(limit=1):
    """Bounded and safe to resume after a restart."""
    candidates = KaspiReviewTask.objects.filter(
        status='pending', batch__status='done', offer__match_verified=False,
    ).exclude(offer__kaspi_url='').order_by('priority', 'pk').values_list('pk', flat=True)[:max(0, min(int(limit), 2))]
    results = []
    for pk in list(candidates):
        result = verify_bound_card(pk)
        results.append(result)
        if result == 'source_blocked':
            break
    return results


def resume_source_blocked_after_recovery(*, reader=read_public_product_page):
    """One public-source recovery probe a day; never bypass source denial.

    Exact source metadata must be readable before previously access-blocked
    jobs are rescheduled. This does not renew any Kaspi price or stock.
    """
    from django.core.cache import cache
    if not KaspiReviewTask.objects.filter(
        status='blocked', reason__startswith='Источник Kaspi ограничил доступ.',
        batch__status='done',
    ).exists():
        return 0
    today = timezone.localdate()
    key = 'procurement:kaspi-source-recovery:' + today.isoformat()
    if not cache.add(key, 'probing', timeout=86400):
        return 0
    sample = KaspiReviewTask.objects.filter(
        status='blocked', reason__startswith='Источник Kaspi ограничил доступ.',
        batch__status='done', offer__match_verified=False,
    ).exclude(offer__kaspi_url='').select_related('offer').order_by('pk').first()
    if sample is None:
        return 0
    try:
        evidence = validate_public_identity(sample.offer, reader(sample.offer.kaspi_url))
    except (requests.RequestException, ValueError) as error:
        logger.warning('Kaspi public source recovery: unavailable (%s); no retry today',
                       type(error).__name__)
        return 0
    if not evidence:
        logger.warning('Kaspi public source recovery: exact product evidence unavailable; no retry today')
        return 0
    count = KaspiReviewTask.objects.filter(
        status='blocked', reason__startswith='Источник Kaspi ограничил доступ.',
        batch__status='done',
    ).exclude(offer__kaspi_url='').update(
        status='pending', reason='Источник восстановлен; ожидает точной проверки товара.')
    logger.info('Kaspi source recovered; rescheduled %s exact-card checks', count)
    return count
