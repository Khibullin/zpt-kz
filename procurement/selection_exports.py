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

JSON_BATCH_SIZE = 1000
EXPORT_BATCH_SIZES = (500, 1000, 1500, 2000)


def export_batch_size(value):
    try:
        size = int(value)
    except (ValueError, TypeError):
        raise ValueError('Размер партии: 500, 1000, 1500 или 2000 запросов.')
    if size not in EXPORT_BATCH_SIZES:
        raise ValueError('Размер партии: 500, 1000, 1500 или 2000 запросов.')
    return size

from .apify_cities import APIFY_CITIES
from .kaspi_search_queries import MAX_ITEMS_PER_QUERY
APIFY_MAX_ITEMS_PER_QUERY = MAX_ITEMS_PER_QUERY


def filtered_offers(rule):
    """Use exactly the same criteria as the saved preview, without mutating offers."""
    # Temporary Omega-only working scope; preserve other supplier records for audited cleanup.
    rows = Offer.objects.filter(supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    if rule.supplier_id:
        rows = rows.filter(supplier_id=rule.supplier_id)
    rows = rows.filter(cost__gte=rule.min_cost)
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
    """Build query-to-offer mapping before pagination: batch size is selected at export."""
    seen = set()
    result = []
    from .kaspi_search_queries import procurement_search_queries
    rows = filtered_offers(rule).values('id', 'brand', 'article', 'name').iterator(chunk_size=1000)
    for offer in rows:
        brand = str(offer['brand'] or '').strip()
        for article in article_variants(offer['article']):
            for query in procurement_search_queries(brand, article, offer['name']):
                key = query.casefold()
                if not brand or key in seen:
                    continue
                seen.add(key)
                result.append({'query': query, 'offer_id': offer['id'],
                               'brand': brand, 'article': article,
                               'original_article': offer['article']})
    return result


def known_card_urls(rows, supplier_id=None):
    from .apify_price_recheck import _brand, normalize_article
    from .models import KaspiPriceAudit
    from urllib.parse import urlsplit
    identities = {(_brand(row.get('brand')), normalize_article(row.get('original_article') or row.get('article'))) for row in rows}
    brands = {row.get('brand') for row in rows if row.get('brand')}
    condition = Q(pk__in=[])
    for brand in brands:
        condition |= Q(brand__iexact=brand)
        if _brand(brand) == 'parts-mall':
            condition |= Q(brand__iexact='PARTS-MALL') | Q(brand__iexact='PMC PARTS MALL') | Q(brand__iexact='PARTS MALL')
    offers = Offer.objects.filter(condition, supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    if supplier_id:
        offers = offers.filter(supplier_id=supplier_id)
    matched = [offer for offer in offers.only('id', 'brand', 'article', 'kaspi_url')
               if (_brand(offer.brand), normalize_article(offer.article)) in identities]
    candidates = [offer.kaspi_url for offer in matched]
    candidates.extend(KaspiPriceAudit.objects.filter(offer_id__in=[offer.pk for offer in matched]).values_list('card_url', flat=True))
    urls = set()
    for value in candidates:
        try:
            url = urlsplit(str(value or ''))
        except ValueError:
            continue
        if (url.scheme == 'https' and url.netloc == 'kaspi.kz'
                and url.path.startswith('/shop/p/') and re.search(r'-\d{6,15}/?$', url.path)):
            urls.add('https://kaspi.kz' + url.path)
    return sorted(urls)


@access('sales', 'admin')
@require_GET
def selection_apify_json(request):
    try:
        batch_size = export_batch_size(request.GET.get('batch_size', JSON_BATCH_SIZE))
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
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
            mapping = request_queries(saved)
            request.session['apify_prepared_request_id'] = saved.pk
        except (ValueError, TypeError, KeyError):
            return JsonResponse({'error': 'Некорректный запрос.'}, status=400)
    else:
        mapping = apify_queries(rule)
    start = (batch - 1) * batch_size
    rows = mapping[start:start + batch_size]
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
        return JsonResponse({'error': 'Число карточек должно быть от 1 до 20.'}, status=400)
    if not 1 <= cards <= MAX_ITEMS_PER_QUERY:
        return JsonResponse({'error': 'Число карточек должно быть от 1 до 20.'}, status=400)
    if request.GET.get('format') == 'mapping':
        payload = {'batch': batch, 'source': saved.details.get('supplier_name', 'Omega') if saved_id else str(rule.supplier or ''), 'request_id': saved_id, 'mapping': rows}
        filename = f'apify_mapping_{saved_id}_{batch:02d}.json' if saved_id else f'apify_mapping_{batch:02d}.json'
    else:
        modern = not saved_id or saved.details.get('search_policy') == 'name_article_v5'
        payload = {
            'searchQueries': [row['query'] for row in rows],
            'startUrls': known_card_urls(rows, saved.details.get('supplier_id') if saved_id else rule.supplier_id),
            'cities': cities,
            'sortOrder': 'price-asc' if modern else 'relevance',
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
    if batch_size != 500:
        filename = filename.removesuffix('.json') + f'_size{batch_size}.json'
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=2),
                            content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
