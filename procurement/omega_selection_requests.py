"""Non-destructive Omega category selections based on staged supplier price files.

Every saved request is a snapshot of criteria and source pricelist identity.
Nothing here updates Offers, stock, current Kaspi observations or selections.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from datetime import date
import re

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from .analysis import stock_status
from .models import Activity, PriceUpdate
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


def latest_price_batch():
    return (PriceUpdate.objects.filter(supplier__name='Omega')
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


def pick_rows(batch, categories, minimum, max_age, in_stock_only=True, exclude_springs=False):
    keys = {category_key(v) for v in categories}
    selected, unknown = [], 0
    for row in iter_price_rows(batch.pk):
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


def category_usage(saved_requests):
    """Show previous request numbers without removing repeatable categories."""
    usage = defaultdict(list)
    has_first = any(int(item.details.get('number', 0)) == 1 for item in saved_requests)
    if not has_first:
        for name in DEFAULT_CATEGORIES:
            usage[category_key(name)].append(1)
    for item in saved_requests:
        for name in item.details.get('category_keys', []):
            number = int(item.details.get('number', 0))
            key = category_key(name)
            if number and number not in usage[key]:
                usage[key].append(number)
    return {key: sorted(numbers) for key, numbers in usage.items()}


def get_display_history(saved_requests):
    """Historical request is shown as a disclosure, not falsified as an Apify run."""
    history = [{'id': item.pk, 'details': item.details,
                'created_at': item.created_at, 'legacy': False,
                'export_batches': range(1, (int(item.details.get('query_count') or 0) + 499) // 500 + 1)}
               for item in saved_requests]
    if not any(int(item['details'].get('number', 0)) == 1 for item in history):
        history.insert(0, {'id': None, 'details': LEGACY_FIRST_REQUEST,
                           'created_at': None, 'legacy': True, 'export_batches': []})
    return history


def request_history():
    return (Activity.objects.filter(action=REQUEST_ACTION)
            .select_related('user').order_by('pk'))


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
                     bool(details.get('exclude_springs', False)))[0]


def request_queries(saved):
    from .selection_exports import article_variants
    seen = {}
    for row in request_rows(saved):
        brand = str(row.get('brand') or '').strip()
        original = str(row.get('article') or '').strip()
        for article in article_variants(original):
            query = ' '.join((brand, article))
            key = query.casefold()
            if key not in seen:
                seen[key] = {'query': query, 'brand': brand,
                             'article': article, 'original_article': original}
    return list(seen.values())


@transaction.atomic
def save_request(*, batch_id, categories, minimum, user,
                 max_vehicle_age=20, in_stock_only=True, exclude_springs=False):
    batch = PriceUpdate.objects.select_for_update().defer('rows').get(
        pk=batch_id, supplier__name='Omega')
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
    filtered, unknown_age = pick_rows(batch, requested, minimum, max_vehicle_age, in_stock_only, exclude_springs)
    if not filtered:
        raise ValidationError('По выбранным условиям товары не найдены.')
    previous = list(Activity.objects.select_for_update()
                    .filter(action=REQUEST_ACTION).only('id', 'details'))
    number = max((int(item.details.get('number', 0)) for item in previous), default=1) + 1
    from .selection_exports import article_variants
    queries = set()
    for row in filtered:
        for article in article_variants(row.get('article')):
            queries.add((' '.join((str(row['brand']).strip(), article))).casefold())
    return Activity.objects.create(user=user, action=REQUEST_ACTION, details={
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
    batch = PriceUpdate.objects.defer('rows').get(pk=batch_id, supplier__name='Omega')
    available = {row['key'] for row in price_categories(batch)}
    keys = {category_key(x) for x in categories}
    if not keys.issubset(available):
        raise ValidationError('Категории не найдены в выбранном прайсе.')
    selected, unknown_age = pick_rows(batch, keys, minimum, max_vehicle_age, in_stock_only, exclude_springs)
    from .selection_exports import article_variants, JSON_BATCH_SIZE
    queries = {(' '.join((str(row['brand']).strip(), variant))).casefold()
               for row in selected for variant in article_variants(row['article'])}
    return {'categories': len(keys), 'products': len(selected),
            'queries': len(queries),
            'batches': (len(queries) + JSON_BATCH_SIZE - 1) // JSON_BATCH_SIZE,
            'unknown_age': unknown_age}
