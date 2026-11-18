"""Import Apify Kaspi search export into existing supplier offers, conservatively.

This importer never creates supplier items, changes stock/cost, or treats search
results as identity proof. The original JSON remains the source of truth.
"""
import json
import re
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Supplier, Offer, KaspiSellerSnapshot, Activity
from .matching import normalize, compare_identity


def _compatible_existing_card(offer, product):
    """Existing URL is only a hint; require part-type and side consistency too."""
    if offer.kaspi_url != product.get('url'):
        return False
    title = normalize(product.get('title'))
    category = normalize(product.get('category'))
    name = normalize(offer.name)
    for side in ('передний', 'задний', 'левый', 'правый', 'верхний', 'нижний'):
        if re.search(r'(?<!\\w)' + side + r'(?!\\w)', name) and not re.search(r'(?<!\\w)' + side + r'(?!\\w)', title):
            return False
    stop = {'передний', 'задний', 'левый', 'правый', 'верхний', 'нижний', 'комплект', 'шт', 'для', 'авто'}
    name_words = [word for word in re.findall(r'[\\w]+', name) if len(word) >= 4 and word not in stop]
    # Reject a title/category that has none of the supplier's distinctive part type.
    return bool(name_words) and any(word[:6] in title or word[:6] in category for word in name_words)


def import_apify_kaspi(file, supplier_name, date, user):
    all_suppliers = supplier_name == 'Все поставщики'
    supplier = None if all_suppliers else Supplier.objects.filter(name=supplier_name.strip()).first()
    if not all_suppliers and not supplier:
        raise ValueError('Сначала выберите существующего поставщика.')
    if date > timezone.localdate():
        raise ValueError('Дата проверки не может быть в будущем.')
    try:
        payload = json.load(file)
    except (ValueError, UnicodeError):
        raise ValueError('Нужен исходный JSON Apify.')
    if not isinstance(payload, list) or not 1 <= len(payload) <= 20000:
        raise ValueError('Ожидается список 1–20 000 результатов Apify.')
    # The supplier is already known; do not inject new products into its price list.
    by_identity = {}
    supplier_offers = Offer.objects.filter(supplier__name__in=['Omega', 'Фаетон', 'ZakazAuto — Костанай']) if all_suppliers else Offer.objects.filter(supplier=supplier)
    for offer in supplier_offers.only(
            'id', 'brand', 'article', 'name', 'kaspi_url', 'match_verified',
            'kaspi_price_checked_on').iterator(chunk_size=2000):
        if offer.article and offer.brand:
            by_identity.setdefault((normalize(offer.brand), normalize(offer.article)), []).append(offer)
    candidates = {}
    skipped = 0
    for product in payload:
        if not isinstance(product, dict) or product.get('error'):
            skipped += 1
            continue
        if (str(product.get('cityId')) != '750000000' or product.get('currency') != 'KZT' or
                not str(product.get('category') or '').startswith('Автотовары > Автозапчасти') or
                not product.get('inStock') or not isinstance(product.get('offers'), list)):
            skipped += 1
            continue
        brand = normalize(product.get('brand'))
        title = normalize(product.get('title'))
        url = str(product.get('url') or '')
        product_id = str(product.get('id') or '')
        if not brand or not product_id.isdigit() or not re.fullmatch(
                r'https://kaspi\.kz/shop/p/[^?#]+-' + re.escape(product_id) + r'/?', url):
            skipped += 1
            continue
        # Search by actual product title, NOT searchQuery: it can point to unrelated goods.
        tokens = set(re.findall(r'[\w]+(?:-[\w]+)*', title))
        for article in tokens:
            for offer in by_identity.get((brand, article), []):
                confirmed, _ = compare_identity(offer, product.get('title'), offer.article, product.get('brand'))
                if not confirmed and not _compatible_existing_card(offer, product):
                    continue
                # One supplier item may have several Kaspi IDs. Do not choose arbitrarily.
                candidates.setdefault(offer.pk, {})[product_id] = (offer, product)
    imported = 0
    with transaction.atomic():
        for matches in candidates.values():
            # If Kaspi returns multiple IDs for one SKU, use only a card whose
            # URL was already bound to this supplier item; otherwise review.
            if len(matches) == 1:
                offer, product = next(iter(matches.values()))
            else:
                bound = [(offer, product) for offer, product in matches.values() if offer.kaspi_url == product['url']]
                if len(bound) != 1:
                    skipped += 1
                    continue
                offer, product = bound[0]
            if offer.kaspi_url and offer.kaspi_url != product['url']:
                skipped += 1
                continue
            if offer.kaspi_price_checked_on and offer.kaspi_price_checked_on > date:
                skipped += 1
                continue
            sellers = {}
            for row in product.get('offers') or []:
                if not isinstance(row, dict):
                    continue
                merchant = str(row.get('sellerId') or '')
                name = str(row.get('sellerName') or '').strip()
                try:
                    price = Decimal(str(row.get('price')))
                except (TypeError, ValueError, ArithmeticError):
                    continue
                if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', merchant)
                        or not name or len(name) > 250 or not price.is_finite() or price <= 0):
                    continue
                previous = sellers.get(merchant)
                if previous and Decimal(previous['price']) <= price:
                    continue
                sellers[merchant] = {
                    'merchant_id': merchant, 'name': name,
                    'url': f'https://kaspi.kz/shop/m/{merchant}/',
                    'price': str(price), 'rating': row.get('sellerRating'),
                    'reviews': row.get('sellerReviews'),
                }
            if not sellers:
                skipped += 1
                continue
            locked = Offer.objects.select_for_update().get(pk=offer.pk)
            if (locked.kaspi_url and locked.kaspi_url != product['url']) or (locked.kaspi_price_checked_on and locked.kaspi_price_checked_on > date):
                skipped += 1
                continue
            price = min(Decimal(item['price']) for item in sellers.values())
            locked.kaspi_url = product['url']
            locked.kaspi_price = price
            locked.kaspi_price_checked_on = date
            locked.match_verified = True
            # Explicit provenance: this is a dated Apify snapshot, not live availability.
            locked.characteristics = {
                **(locked.characteristics or {}),
                'kaspi_identity': {
                    'source_url': product['url'], 'source_date': date.isoformat(),
                    'checked_on': date.isoformat(), 'title': product['title'],
                    'article': locked.article, 'brand': product['brand'],
                    'confirmed': True, 'method': 'apify_exact_title_brand_article_v1',
                },
            }
            locked.save(update_fields=['kaspi_url', 'kaspi_price',
                                       'kaspi_price_checked_on', 'match_verified', 'characteristics'])
            KaspiSellerSnapshot.objects.update_or_create(offer=locked, defaults={
                'source_url': product['url'], 'city': '750000000',
                'checked_on': date, 'complete': False,
                'sellers': list(sellers.values()),
            })
            imported += 1
            if locked.supplier_id:
                from .opportunities import capture_profitable_offer
                capture_profitable_offer(locked)
        Activity.objects.create(user=user, action='competitors_import',
                                details={'source': 'apify', 'supplier': 'Все поставщики' if all_suppliers else supplier.name,
                                         'matched': imported, 'skipped': skipped, 'date': date.isoformat()})
    return imported
