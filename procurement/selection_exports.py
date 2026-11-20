"""Read-only exports of the saved wholesale pricelist selection.

No Kaspi request or Apify run is made by these endpoints.
"""
import json
import re
from datetime import date
from io import BytesIO

from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import Offer, WholesaleSelectionRule
from .selection_rule_views import _stock_filter, _group_filter
from .views import access

JSON_BATCH_SIZE = 500
APIFY_CITIES = ['Almaty', 'Astana', 'Shymkent', 'Karaganda', 'Aktobe', '391010000']


def filtered_offers(rule):
    """Use exactly the same criteria as the saved preview, without mutating offers."""
    # Temporary Omega-only working scope; preserve other supplier records for audited cleanup.
    rows = Offer.objects.filter(supplier__name='Omega')
    rows = rows.filter(cost__gte=rule.min_cost)
    if rule.in_stock_only:
        rows = rows.filter(_stock_filter())
    rows = rows.filter(_group_filter(rule.category_groups, rule.classification_mode))
    years = range(max(1980, date.today().year - rule.max_vehicle_age), date.today().year + 2)
    year_pattern = '(' + '|'.join(str(year) for year in years) + ')'
    return rows.filter(
        Q(applicability__regex=year_pattern) |
        Q(fitments__verified=True, fitments__details__regex=year_pattern)
    ).distinct().order_by('supplier_id', 'brand', 'article', 'pk')


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
    mapping = apify_queries(rule)
    start = (batch - 1) * JSON_BATCH_SIZE
    rows = mapping[start:start + JSON_BATCH_SIZE]
    if not rows:
        return JsonResponse({'error': 'Нет запросов в этой партии.'}, status=404)
    if request.GET.get('format') == 'mapping':
        payload = {'batch': batch, 'source': 'Omega', 'mapping': rows}
        filename = f'apify_mapping_{batch:02d}.json'
    else:
        payload = {
            'searchQueries': [row['query'] for row in rows],
            'cities': APIFY_CITIES,
            'sortOrder': 'price-asc',
            'maxItems': 0,
            'maxItemsPerQuery': 0,
            'includeOffers': True,
            'maxOffersPerProduct': 0,
            'includeReviews': False,
            'proxyConfiguration': {
                'useApifyProxy': True,
                'apifyProxyGroups': ['RESIDENTIAL'],
                'apifyProxyCountry': 'KZ',
            },
        }
        filename = f'apify_input_{batch:02d}.json'
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2),
                            content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
