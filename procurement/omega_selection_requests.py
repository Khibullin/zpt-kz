"""Non-destructive Omega category selections based on staged supplier price files.

Every saved request is a snapshot of criteria and source pricelist identity.
Nothing here updates Offers, stock, current Kaspi observations or selections.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import date
import re

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from .analysis import stock_status
from .models import Activity, PriceUpdate, Supplier
from .price_source import CATEGORY_FIELDS, iter_price_rows

DEFAULT_CATEGORIES = (
    'Амортизатор', 'Колодки', 'Шрус (граната)', 'Рычаг',
    'Рулевой наконечник', 'Диск тормозной', 'Шаровая опора',
    'Опора амортизатора', 'Рулевая тяга', 'Стойка стабилизатора',
)
REQUEST_ACTION = 'omega_category_selection_request'
LEGACY_FIRST_REQUEST = {
    'number': 1, 'title': 'Запрос №1', 'filename': 'Solid Prof Group, 20.11.2026',
    'checked_on': '2026-11-20', 'categories': list(DEFAULT_CATEGORIES),
    'category_keys': [category_key_name.casefold() for category_key_name in DEFAULT_CATEGORIES],
    'min_cost': '2000', 'total': 4214, 'query_count': 4259,
    'status': 'JSON подготовлены, отправлены в Apify вручную; завершение не подтверждено',
}



def category_key(value):
    return ' '.join(str(value or '').strip().split()).casefold()


def latest_price_batch(supplier=None):
    supplier = supplier or Supplier.objects.filter(name='Omega').first()
    return (PriceUpdate.objects.filter(supplier=supplier)
            .only('id', 'filename', 'checked_on', 'sha256', 'row_count', 'created_at')
            .order_by('-checked_on', '-pk').first())


def _cost(row):
    try:
        amount = Decimal(str(row.get('cost') or 0).replace(',', '.'))
        return amount if amount.is_finite() else Decimal('-1')
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('-1')


def eligible_row(row, minimum):
    return (
        not row.get('_conflict')
        and bool(str(row.get('brand') or '').strip())
        and bool(str(row.get('article') or '').strip())
        and stock_status(str(row.get('stock') or '')) == 'in_stock'
        and _cost(row) >= minimum
    )


def fitment_age(row, max_age):
    """Only constrain dated applicability; missing year stays review-required."""
    source = str(row.get('applicability') or '')
    years = [int(v) for v in re.findall(r'(?<![0-9])(?:19[89][0-9]|20[0-4][0-9])(?![0-9])', source)]
    if not years:
        return 'unknown'
    earliest = max(1980, date.today().year - max_age)
    return 'within' if any(earliest <= year <= date.today().year + 1 for year in years) else 'older'


def _is_spring(row):
    text = ' '.join(str(row.get(field) or '') for field in ('name', 'category', 'title')).casefold()
    return 'пружин' in text or 'spring' in text


def fresh_checked_keys(batch):
    """Skip only already audited, verified, unchanged variants, for seven days."""
    if batch.supplier.name == 'Omega':
        return set()  # Preserve the existing Omega request protocol.
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import F
    from .models import KaspiPriceAudit
    now = timezone.now()
    audits = KaspiPriceAudit.objects.filter(
        offer__supplier=batch.supplier, offer__match_verified=True,
        observed_at__gte=now-timedelta(days=7), observed_at__lte=now,
        card_url=F('offer__kaspi_url'), cost_snapshot=F('offer__cost'),
    ).select_related('offer').only('card_url', 'cost_snapshot',
        'offer__brand', 'offer__article', 'offer__name', 'offer__characteristics')
    if batch.supplier.name == 'Phaeton DC':
        from .minimum_price_policy import reviewed_minimum_query
        from django.db.models import OuterRef, Subquery
        latest = KaspiPriceAudit.objects.filter(offer_id=OuterRef('offer_id')).order_by('-observed_at', '-pk')
        audits = audits.filter(reviewed_minimum_query(), pk=Subquery(latest.values('pk')[:1]))
    return {row_reuse_key(dict(brand=a.offer.brand, article=a.offer.article,
        name=a.offer.name, cost=str(a.cost_snapshot),
        _pack_size=a.offer.characteristics.get('supplier_pack_size', 1))) for a in audits}


def row_reuse_key(row):
    return (str(row.get('brand') or '').strip().casefold(),
            str(row.get('article') or '').strip().casefold(),
            str(row.get('name') or '').strip(),
            str(_cost(row).normalize()), str(row.get('_pack_size') or 1))


def pick_rows(batch, categories, minimum, max_age, in_stock_only=True, exclude_springs=False, excluded_keys=()):
    keys = {category_key(v) for v in categories}
    selected, unknown = [], 0
    excluded_keys = set(tuple(key) for key in excluded_keys)
    for row in iter_price_rows(batch.pk):
        if row_reuse_key(row) in excluded_keys:
            continue
        if category_key(row.get('category') or 'Без категории') not in keys:
            continue
        if row.get('_conflict') or not row.get('brand') or not row.get('article') or _cost(row) < minimum:
            continue
        if exclude_springs and _is_spring(row):
            continue
        if in_stock_only and stock_status(str(row.get('stock') or '')) != 'in_stock':
            continue
        status = fitment_age(row, max_age)
        if status == 'older':
            continue
        if status == 'unknown':
            unknown += 1
        selected.append(row)
    return selected, unknown


def price_categories(batch, minimum=Decimal('2000')):
    """Category counts from original staged lines, never inferred from name."""
    key = 'omega:staged-categories:v2:%s:%s:%s' % (batch.pk, batch.sha256, minimum)
    cached = cache.get(key)
    if cached is not None:
        return cached
    counts = defaultdict(lambda: {'total': 0, 'in_stock': 0, 'eligible': 0})
    for row in iter_price_rows(batch.pk, fields=CATEGORY_FIELDS):
        name = ' '.join(str(row.get('category') or '').strip().split()) or 'Без категории'
        info = counts[name]
        info['total'] += 1
        available = stock_status(str(row.get('stock') or '')) == 'in_stock'
        if available:
            info['in_stock'] += 1
        if eligible_row(row, minimum):
            info['eligible'] += 1
    result = [{'name': name, 'key': category_key(name), **value}
              for name, value in sorted(counts.items(), key=lambda pair: pair[0].casefold())]
    cache.set(key, result, timeout=300)
    return result


def category_usage(saved_requests, include_legacy=True):
    """Show previous request numbers without removing repeatable categories."""
    usage = defaultdict(list)
    has_first = any(int(item.details.get('number', 0)) == 1 for item in saved_requests)
    if include_legacy and not has_first:
        for name in DEFAULT_CATEGORIES:
            usage[category_key(name)].append(1)
    for item in saved_requests:
        for name in item.details.get('category_keys', []):
            number = int(item.details.get('number', 0))
            key = category_key(name)
            if number and number not in usage[key]:
                usage[key].append(number)
    return {key: sorted(numbers) for key, numbers in usage.items()}


def get_display_history(saved_requests, include_legacy=True, batch_size=1000):
    """Historical request is shown as a disclosure, not falsified as an Apify run."""
    history = [{'id': item.pk, 'details': item.details,
                'created_at': item.created_at, 'legacy': False,
                'export_batches': range(1, (int(item.details.get('query_count') or 0) + batch_size - 1) // batch_size + 1)}
               for item in saved_requests]
    if include_legacy and not any(int(item['details'].get('number', 0)) == 1 for item in history):
        history.insert(0, {'id': None, 'details': LEGACY_FIRST_REQUEST,
                           'created_at': None, 'legacy': True, 'export_batches': []})
    return history


def request_history(supplier=None):
    from django.db.models import Q
    supplier = supplier or Supplier.objects.filter(name='Omega').first()
    if supplier is None:
        return Activity.objects.none()
    scope = Q(details__supplier_id=supplier.pk)
    if supplier.name == 'Omega':
        scope |= Q(details__supplier_id__isnull=True)
    return Activity.objects.filter(scope, action=REQUEST_ACTION).select_related('user').order_by('pk')


def request_rows(saved):
    """Read the immutable source batch, not today's offers or price."""
    details = saved.details
    batch = PriceUpdate.objects.only('sha256').get(pk=details['price_batch_id'])
    if batch.sha256 != details['price_sha256']:
        raise ValidationError('Исходный прайс изменился. Запрос нельзя воспроизвести.')
    minimum = Decimal(details['min_cost'])
    keys = set(details['category_keys'])
    return pick_rows(batch, keys, minimum, int(details.get('max_vehicle_age', 20)),
                     bool(details.get('in_stock_only', True)),
                     bool(details.get('exclude_springs', False)), details.get('fresh_checked_keys', []))[0]


def request_queries(saved):
    from .selection_exports import article_variants
    from .kaspi_search_queries import sku_search_queries
    seen = {}
    modern = saved.details.get('search_policy') in ('name_article_v4', 'name_article_v5')
    from .kaspi_search_queries import monitoring_search_queries, procurement_search_queries
    builder = procurement_search_queries if saved.details.get('search_policy') == 'name_article_v5' else monitoring_search_queries
    search_supplier = (saved.details.get('supplier_name', 'Omega')
        if saved.details.get('search_policy') == 'phaeton_article_queries_v2' else 'Omega')
    for row in request_rows(saved):
        brand = str(row.get('brand') or '').strip()
        original = str(row.get('article') or '').strip()
        for article in article_variants(original):
            queries = (builder(brand, article, row.get('name', '')) if modern
                       else sku_search_queries(brand, article, search_supplier,
                           legacy=saved.details.get('search_policy') != 'article_only_v3'))
            for query in queries:
                key = query.casefold()
                if key not in seen:
                    seen[key] = {'query': query, 'brand': brand,
                                 'article': article, 'original_article': original}
    return list(seen.values())


@transaction.atomic
def save_request(*, batch_id, categories, minimum, user,
                 max_vehicle_age=20, in_stock_only=True, exclude_springs=False):
    supplier_id = PriceUpdate.objects.values_list('supplier_id', flat=True).get(
        pk=batch_id, supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    Supplier.objects.select_for_update().get(pk=supplier_id)
    batch = PriceUpdate.objects.select_for_update().defer('rows').get(pk=batch_id)
    try:
        minimum = Decimal(str(minimum).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Некорректная минимальная закупочная цена.')
    if not minimum.is_finite() or minimum < 0 or minimum > 999999999999:
        raise ValidationError('Некорректная минимальная закупочная цена.')
    available = {row['key']: row['name'] for row in price_categories(batch, minimum)}
    unique = list(dict.fromkeys(category_key(name) for name in categories if name))
    if not unique or len(unique) > 300 or any(key not in available for key in unique):
        raise ValidationError('Выберите действительные категории из загруженного прайса.')
    try:
        max_vehicle_age = int(max_vehicle_age)
    except (ValueError, TypeError):
        raise ValidationError('Возраст автомобиля должен быть целым числом.')
    if not 0 <= max_vehicle_age <= 100:
        raise ValidationError('Допустимый возраст: от 0 до 100 лет.')
    requested = set(unique)
    excluded_keys = set()
    filtered, unknown_age = pick_rows(batch, requested, minimum, max_vehicle_age, in_stock_only, exclude_springs, excluded_keys)
    if not filtered:
        raise ValidationError('По выбранным условиям товары не найдены.')
    from .request_snapshot_import import validate_snapshot_rows
    validate_snapshot_rows(filtered)
    previous = list(request_history(batch.supplier).select_related(None).select_for_update().only('id', 'details'))
    number = max((int(item.details.get('number', 0)) for item in previous),
                 default=1 if batch.supplier.name == 'Omega' else 0) + 1
    from .selection_exports import article_variants
    from .kaspi_search_queries import procurement_search_queries
    queries = set()
    for row in filtered:
        for article in article_variants(row.get('article')):
            queries.update(q.casefold() for q in procurement_search_queries(row['brand'], article, row.get('name', '')))
    return Activity.objects.create(user=user, action=REQUEST_ACTION, details={
        'supplier_id': batch.supplier_id, 'supplier_name': batch.supplier.name,
        'search_policy': 'name_article_v5', 'processing_policy': 'request_snapshot_v1',
        'fresh_checked_keys': sorted(excluded_keys),
        'number': number, 'title': f'Запрос №{number}',
        'price_batch_id': batch.pk, 'price_sha256': batch.sha256,
        'filename': batch.filename, 'checked_on': batch.checked_on.isoformat(),
        'categories': [available[key] for key in unique],
        'category_keys': unique, 'min_cost': str(minimum),
        'total': len(filtered), 'query_count': len(queries),
        'max_vehicle_age': max_vehicle_age, 'unknown_age': unknown_age,
        'in_stock_only': bool(in_stock_only), 'exclude_springs': bool(exclude_springs),
        'status': 'Подготовлен',
    })

def preview_request(*, batch_id, categories, minimum, max_vehicle_age, in_stock_only=True, exclude_springs=False):
    try:
        minimum = Decimal(str(minimum).replace(',', '.'))
        max_vehicle_age = int(max_vehicle_age)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Проверьте цену закупки и возраст автомобиля.')
    if not minimum.is_finite() or minimum < 0 or not 0 <= max_vehicle_age <= 100:
        raise ValidationError('Некорректные параметры отбора.')
    batch = PriceUpdate.objects.defer('rows').get(pk=batch_id, supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    available = {row['key'] for row in price_categories(batch)}
    keys = {category_key(x) for x in categories}
    if not keys.issubset(available):
        raise ValidationError('Категории не найдены в выбранном прайсе.')
    selected, unknown_age = pick_rows(batch, keys, minimum, max_vehicle_age, in_stock_only, exclude_springs, ())
    from .request_snapshot_import import validate_snapshot_rows
    validate_snapshot_rows(selected)
    from .selection_exports import article_variants, JSON_BATCH_SIZE
    from .kaspi_search_queries import procurement_search_queries
    queries = {q.casefold() for row in selected for variant in article_variants(row['article'])
               for q in procurement_search_queries(row['brand'], variant, row.get('name', ''))}
    return {'categories': len(keys), 'products': len(selected),
            'queries': len(queries),
            'batches': (len(queries) + JSON_BATCH_SIZE - 1) // JSON_BATCH_SIZE,
            'unknown_age': unknown_age}
