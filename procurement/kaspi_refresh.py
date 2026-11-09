"""Read-only refresh of already verified bound cards; never infer card identity."""
import io
import json
import time
import re
from decimal import Decimal
from urllib.parse import urlsplit
import requests
from django.db import transaction
from django.utils import timezone
from integrations.kaspi_competitors import kaspi_public_product_id
from .importing import import_competitors
from .models import KaspiReviewTask, Offer, KaspiSellerSnapshot


class KaspiRefreshError(ValueError):
    pass


def read_complete_sellers(url, session=None, pause=time.sleep):
    product_id = kaspi_public_product_id('', public_url=url)
    if not product_id:
        raise KaspiRefreshError('Не подтверждён идентификатор карточки.')
    session = session or requests.Session()
    sellers = {}
    for page in range(50):
        response = session.post('https://kaspi.kz/yml/offer-view/offers/' + product_id,
            json={'cityId': '750000000', 'id': product_id, 'merchantUID': '',
                  'limit': 32, 'page': page, 'sort': True, 'installationId': '-1'},
            headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                     'Origin': 'https://kaspi.kz', 'Referer': url,
                     'User-Agent': 'ZPT-KZ-Procurement/1.0 (+https://zpt.kz)',
                     'X-KS-City': '750000000'}, timeout=10)
        if response.status_code != 200:
            raise KaspiRefreshError(f'Источник Kaspi недоступен: HTTP {response.status_code}. Проверка приостановлена.')
        try:
            data = response.json()
        except ValueError:
            raise KaspiRefreshError('Kaspi вернул нечитаемый ответ.')
        rows = data.get('offers') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise KaspiRefreshError('Нет списка продавцов в ответе Kaspi.')
        if not rows:
            return list(sellers.values())
        previous = len(sellers)
        for row in rows:
            if not isinstance(row, dict):
                raise KaspiRefreshError('Неполные данные продавцов.')
            merchant = str(row.get('merchantId') or '')
            name = str(row.get('merchantName') or '').strip()
            try:
                price = Decimal(str(row.get('price')))
            except ArithmeticError:
                raise KaspiRefreshError('Не прочитана цена продавца.')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', merchant) or not name or not price.is_finite() or price <= 0:
                raise KaspiRefreshError('Неполные данные продавца или цены.')
            item = {'merchant_id': merchant, 'name': name, 'price': str(price),
                    'url': f'https://kaspi.kz/shop/m/{merchant}/', 'rating': None, 'reviews': None}
            if merchant not in sellers or price < Decimal(sellers[merchant]['price']):
                sellers[merchant] = item
        if len(sellers) == previous:
            raise KaspiRefreshError('Пагинация Kaspi повторила страницу; полнота не подтверждена.')
        pause(1)
    raise KaspiRefreshError('Достигнут предел страниц; полнота не подтверждена.')


def refresh_task(task_id, reader=read_complete_sellers):
    task = KaspiReviewTask.objects.select_related('offer', 'batch__user').get(pk=task_id)
    offer = task.offer
    if task.status != 'pending' or not task.batch_id or not offer.match_verified or not offer.kaspi_url:
        return False
    claimed = timezone.now()
    if not KaspiReviewTask.objects.filter(pk=task.pk, status='pending').update(status='checking', updated_at=claimed):
        return False
    original = (offer.kaspi_url, offer.name, offer.brand, offer.article)
    try:
        saved = KaspiSellerSnapshot.objects.filter(source_url=offer.kaspi_url, complete=True,
            checked_on=timezone.localdate(), city='750000000', offer__match_verified=True,
            offer__brand=offer.brand, offer__article=offer.article, offer__name=offer.name).first()
        sellers = saved.sellers if saved else reader(offer.kaspi_url)
    except (ValueError, requests.RequestException):
        KaspiReviewTask.objects.filter(pk=task.pk, status='checking', updated_at=claimed).update(status='blocked', reason='Источник Kaspi недоступен или не подтвердил полноту. Требуется повторная проверка.')
        KaspiReviewTask.objects.filter(status='pending', batch__isnull=False, offer__match_verified=True).exclude(offer__kaspi_url='').update(status='blocked', reason='Проверки приостановлены после отказа источника Kaspi. Повторите вручную позже.')
        return False
    with transaction.atomic():
        current = Offer.objects.select_for_update().get(pk=offer.pk)
        current_task = KaspiReviewTask.objects.select_for_update().get(pk=task.pk)
        if current_task.status != 'checking' or current_task.updated_at != claimed:
            return False
        if not current.match_verified or (current.kaspi_url, current.name, current.brand, current.article) != original:
            KaspiReviewTask.objects.filter(pk=task.pk).update(status='pending', reason='Товар изменился во время проверки')
            return False
        date = timezone.localdate()
        payload = io.StringIO(json.dumps({'checked_on': date.isoformat(), 'offers': [{
            'brand': offer.brand, 'article': offer.article, 'source_url': offer.kaspi_url,
            'city': '750000000', 'complete': True, 'sellers': sellers}]}))
        import_competitors(payload, offer.supplier.name, date, task.batch.user)
        KaspiReviewTask.objects.filter(pk=task.pk).update(status='done', reason='Продавцы Kaspi проверены')
    return True
