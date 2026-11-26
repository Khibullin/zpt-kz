"""Read-only exports of the saved wholesale pricelist selection.

No Kaspi request or Apify run is made by these endpoints.
"""
import json
import re
from datetime import date
from decimal import Decimal
from io import BytesIO

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import Offer, WholesaleSelectionRule
from .selection_rule_views import _stock_filter, _group_filter, matching_years
from .views import access

JSON_BATCH_SIZE = 500
from .apify_cities import APIFY_CITIES
APIFY_MIN_SUPPLIER_COST = Decimal('2000')
APIFY_MAX_ITEMS_PER_QUERY = 2  # Default; configurable from 1 to 5.


def filtered_offers(rule):
    """Use exactly the same criteria as the saved preview, without mutating offers."""
    # Temporary Omega-only working scope; preserve other supplier records for audited cleanup.
    rows = Offer.objects.filter(supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    if rule.supplier_id:
        rows = rows.filter(supplier_id=rule.supplier_id)
    rows = rows.filter(cost__gte=max(rule.min_cost, APIFY_MIN_SUPPLIER_COST))
    if rule.in_stock_only:
        rows = rows.filter(_stock_filter())
    rows = rows.filter(_group_filter(rule.category_groups, rule.classification_mode))
    return matching_years(rows, rule.max_vehicle_age).order_by('supplier_id', 'brand', 'article', 'pk')


def _safe_excel(value):
    text = str(value if value is not None else '')
    if text.startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return text


@access('sales', 'admin')
@require_GET
def selection_excel(request):
    from openpyxl import Workbook
    rule = get_object_or_404(WholesaleSelectionRule, singleton_key=1)
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Отбор')
    sheet.append(['Поставщик', 'Бренд', 'Артикул', 'Наименование', 'Категория',
                  'Закупочная цена, ₸', 'Остаток (из прайса)', 'Применимость',
                  'Ссылка Kaspi (сохранённая)', 'Совпадение Kaspi подтверждено'])
    rows = filtered_offers(rule).select_related('supplier').iterator(chunk_size=1000)
    for offer in rows:
        sheet.append([_safe_excel(offer.supplier.name), _safe_excel(offer.brand),
                      _safe_excel(offer.article), _safe_excel(offer.name),
                      _safe_excel(offer.category), float(offer.cost),
                      _safe_excel(offer.stock), _safe_excel(offer.applicability),
                      _safe_excel(offer.kaspi_url), 'Да' if offer.match_verified else 'Нет'])
    output = BytesIO()
    book.save(output)
    response = HttpResponse(output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="zpt_supplier_selection.xlsx"'
    return response


def article_variants(article):
    """Preserve original SKU and add only a variant without initial numeric zeros."""
    original = str(article or '').strip()
    if not original:
        return []
    variant = re.sub(r'(^|[\s/_-])0+(?=\d)', r'\1', original)
    return [original, variant] if variant != original else [original]


def apify_queries(rule):
    """Build query-to-offer mapping before pagination: one batch is 500 queries."""
    seen = set()
    result = []
    rows = filtered_offers(rule).values('id', 'brand', 'article').iterator(chunk_size=1000)
    for offer in rows:
        brand = str(offer['brand'] or '').strip()
        for article in article_variants(offer['article']):
            query = ' '.join((brand, article)).strip()
            key = query.casefold()
            if not brand or not article or key in seen:
                continue
            seen.add(key)
            result.append({'query': query[:240], 'offer_id': offer['id'],
                           'brand': brand, 'article': article,
                           'original_article': offer['article']})
    return result


@access('sales', 'admin')
@require_GET
def selection_apify_json(request):
    rule = get_object_or_404(WholesaleSelectionRule, singleton_key=1)
    try:
        batch = int(request.GET.get('batch', '1'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Неверный номер партии'}, status=400)
    if not 1 <= batch <= 100000:
        return JsonResponse({'error': 'Неверный номер партии'}, status=400)
    saved_id = request.GET.get('request_id')
    if saved_id:
        from .models import Activity
        from .omega_selection_requests import REQUEST_ACTION, request_queries
        try:
            saved = get_object_or_404(Activity, pk=int(saved_id), action=REQUEST_ACTION)
            if Decimal(str(saved.details.get('min_cost', 0))) < APIFY_MIN_SUPPLIER_COST:
                return JsonResponse({'error': 'Порог закупки в сохранённом запросе ниже 2 000 ₸. Для Apify создайте запрос с минимумом 2 000 ₸.'}, status=400)
            mapping = request_queries(saved)
        except (ValueError, TypeError, KeyError):
            return JsonResponse({'error': 'Некорректный запрос.'}, status=400)
    else:
        mapping = apify_queries(rule)
    start = (batch - 1) * JSON_BATCH_SIZE
    rows = mapping[start:start + JSON_BATCH_SIZE]
    if not rows:
        return JsonResponse({'error': 'Нет запросов в этой партии.'}, status=404)
    # Export-only settings: never modify the saved supplier selection.
    cities = request.GET.getlist('city')
    if not cities:
        cities = list(APIFY_CITIES)
    if any(city not in APIFY_CITIES for city in cities):
        return JsonResponse({'error': 'Неизвестный город поиска.'}, status=400)
    cities = [city for city in APIFY_CITIES if city in cities]
    try:
        cards = int(request.GET.get('cards', str(APIFY_MAX_ITEMS_PER_QUERY)))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Число карточек должно быть от 1 до 5.'}, status=400)
    if not 1 <= cards <= 5:
        return JsonResponse({'error': 'Число карточек должно быть от 1 до 5.'}, status=400)
    if request.GET.get('format') == 'mapping':
        payload = {'batch': batch, 'source': saved.details.get('supplier_name', 'Omega') if saved_id else str(rule.supplier or ''), 'request_id': saved_id, 'mapping': rows}
        filename = f'apify_mapping_{saved_id}_{batch:02d}.json' if saved_id else f'apify_mapping_{batch:02d}.json'
    else:
        payload = {
            'searchQueries': [row['query'] for row in rows],
            'cities': cities,
            'sortOrder': 'relevance',
            'maxItems': 0,
            'maxItemsPerQuery': cards,
            'includeOffers': True,
            'maxOffersPerProduct': 0,
            'includeReviews': False,
            'proxyConfiguration': {
                'useApifyProxy': True,
                'apifyProxyGroups': ['RESIDENTIAL'],
                'apifyProxyCountry': 'KZ',
            },
        }
        filename = f'apify_input_{saved_id}_{batch:02d}.json' if saved_id else f'apify_input_{batch:02d}.json'
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2),
                            content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
