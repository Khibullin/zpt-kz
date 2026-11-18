"""Read-only exports of the saved wholesale pricelist selection.

No Kaspi request or Apify run is made by these endpoints.
"""
import json
from datetime import date
from io import BytesIO

from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import Offer, WholesaleSelectionRule
from .selection_rule_views import _stock_filter, _group_filter
from .views import access

JSON_BATCH_SIZE = 3000


def filtered_offers(rule):
    """Use exactly the same criteria as the saved preview, without mutating offers."""
    rows = Offer.objects.all()
    if rule.supplier_id:
        rows = rows.filter(supplier_id=rule.supplier_id)
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


@access('sales', 'admin')
@require_GET
def selection_apify_json(request):
    rule = get_object_or_404(WholesaleSelectionRule, singleton_key=1)
    try:
        batch = int(request.GET.get('batch', '1'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Неверный номер партии'}, status=400)
    if batch < 1 or batch > 100000:
        return JsonResponse({'error': 'Неверный номер партии'}, status=400)
    start = (batch - 1) * JSON_BATCH_SIZE
    rows = filtered_offers(rule).values(
        'brand', 'article', 'name', 'kaspi_url', 'match_verified',
    )[start:start + JSON_BATCH_SIZE]
    queries, links = [], []
    seen_queries, seen_links = set(), set()
    for item in rows:
        url = (item['kaspi_url'] or '').strip()
        # Only previously verified Kaspi matches qualify as direct product URLs.
        if item['match_verified'] and url.startswith('https://kaspi.kz/shop/'):
            if url not in seen_links:
                links.append(url)
                seen_links.add(url)
            continue
        brand, article, title = (str(item[k] or '').strip() for k in ('brand', 'article', 'name'))
        query = ' '.join(x for x in (brand, article, title) if x)[:240]
        if query and query.casefold() not in seen_queries:
            queries.append(query)
            seen_queries.add(query.casefold())
    if not queries and not links:
        return JsonResponse({'error': 'Нет товаров для выгрузки в этой партии.'}, status=404)
    payload = {
        'searchQueries': queries,
        'startUrls': links,
        'cities': ['Almaty', 'Astana', 'Shymkent'],
        'maxItems': JSON_BATCH_SIZE,
        'maxItemsPerQuery': 3,
        'includeOffers': True,
        'maxOffersPerProduct': 0,
        'includeReviews': False,
        'proxyConfiguration': {
            'useApifyProxy': True,
            'apifyProxyGroups': ['RESIDENTIAL'],
            'apifyProxyCountry': 'KZ',
        },
    }
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2),
                            content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="zpt_apify_input_batch_{batch}.json"'
    )
    return response
