"""User-started, resumable publication of supplier stock. Never contacts Kaspi."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Max, Q, Value
from django.db.models.functions import Concat, Upper, Trim
from django.utils import timezone
from django.utils.text import slugify
from catalog.models import Product, SellerProfile, Category, Brand, CarModel, Country
from .models import Offer, RetailListing, RetailPublicationRun, Activity, VehicleFitment
from .retail import stock_quantity

SUPPLIERS = ['Omega', 'Фаетон', 'ZakazAuto — Костанай']
POLICY = 'supplier_plus_30'


def key_for(offer):
    return offer.brand.strip().upper() + '\x1f' + offer.article.strip().upper()


def stock_price(offer, pack):
    quantity = stock_quantity(offer.stock)
    if offer.cost <= 0:
        return 0, 'Закупочная цена отсутствует.'
    if quantity is None or quantity < pack:
        return 0, 'Нет подтверждённого наличия для минимальной партии.'
    return int((offer.cost * pack * Decimal('1.30')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)), ''


@transaction.atomic
def start_publication(user, missing_only=False):
    SellerProfile.objects.select_for_update().get(slug='ag-parts')
    existing = RetailPublicationRun.objects.filter(status='running').first()
    if existing:
        return existing
    query = Offer.objects.filter(supplier__name__in=SUPPLIERS)
    if missing_only:
        query = query.annotate(_retail_key=Concat(Upper(Trim('brand')), Value('\x1f'), Upper(Trim('article')))).exclude(
            _retail_key__in=RetailListing.objects.filter(product__status='active', source_content__pricing_policy=POLICY).exclude(catalog_key__isnull=True).values('catalog_key'))
        query = query.filter(cost__gt=0, stock__regex=r'^\s*[>≥]?\s*[1-9][0-9]*(\s|$)')
        ids = list(query.values_list('pk', flat=True))
        return RetailPublicationRun.objects.create(user=user, total=len(ids), max_offer_id=max(ids, default=0),
            summary={'offer_ids': ids}, status='running' if ids else 'done')
    return RetailPublicationRun.objects.create(user=user, total=query.count(),
        max_offer_id=query.aggregate(value=Max('pk'))['value'] or 0)


def copy_vehicle_filters(listings):
    """Keep source configurations linked; copy only verified make/model filters."""
    by_offer = {row.offer_id: row.product_id for row in listings}
    fits = list(VehicleFitment.objects.filter(offer_id__in=by_offer, verified=True))
    if not fits:
        return
    country, _ = Country.objects.get_or_create(name='Прочее')
    makes = {fit.make[:100] for fit in fits if fit.make and fit.model}
    brands = {b.name.upper(): b for b in Brand.objects.all()}
    Brand.objects.bulk_create([Brand(name=make, country=country) for make in makes if make.upper() not in brands])
    brands = {b.name.upper(): b for b in Brand.objects.all()}
    relevant_brand_ids = {brands[make.upper()].pk for make in makes}
    models = {(m.brand_id, m.name): m for m in CarModel.objects.filter(brand_id__in=relevant_brand_ids)}
    needed = {(brands[fit.make[:100].upper()].pk, fit.model[:100]) for fit in fits if fit.make and fit.model}
    CarModel.objects.bulk_create([CarModel(brand_id=brand, name=name) for brand, name in needed if (brand, name) not in models], ignore_conflicts=True)
    models = {(m.brand_id, m.name): m for m in CarModel.objects.filter(brand_id__in=relevant_brand_ids)}
    bm, mm = Product.selected_brands.through, Product.selected_models.through
    brand_links, model_links = set(), set()
    for fit in fits:
        if not fit.make or not fit.model:
            continue
        brand_id = brands[fit.make[:100].upper()].pk
        product_id = by_offer[fit.offer_id]
        brand_links.add((product_id, brand_id))
        model_links.add((product_id, models[(brand_id, fit.model[:100])].pk))
    bm.objects.bulk_create([bm(product_id=p, brand_id=b) for p, b in brand_links], ignore_conflicts=True)
    mm.objects.bulk_create([mm(product_id=p, carmodel_id=m) for p, m in model_links], ignore_conflicts=True)


@transaction.atomic
def advance_publication(run_id, batch_size=200):
    run = RetailPublicationRun.objects.select_for_update().get(pk=run_id)
    if run.status != 'running':
        return run
    seller = SellerProfile.objects.select_for_update().get(slug='ag-parts')
    row_query = Offer.objects.filter(supplier__name__in=SUPPLIERS, pk__gt=run.cursor, pk__lte=run.max_offer_id)
    if 'offer_ids' in run.summary:
        pending_ids = sorted(pk for pk in run.summary['offer_ids'] if pk > run.cursor)[:batch_size]
        row_query = row_query.filter(pk__in=pending_ids)
    rows = list(row_query.order_by('pk')[:batch_size])
    if not rows:
        run.status = 'done'
        run.summary.pop('offer_ids', None)
        run.save(update_fields=['status', 'summary', 'updated_at'])
        return run
    keys = {key_for(offer) for offer in rows}
    # Inspect all three sources for each identity, so another source can replace unavailable stock.
    alternatives = Offer.objects.filter(supplier__name__in=SUPPLIERS).annotate(
        _retail_key=Concat(Upper(Trim('brand')), Value('\x1f'), Upper(Trim('article')))).filter(_retail_key__in=keys)
    best = {}
    for offer in alternatives:
        price, reason = stock_price(offer, max(1, offer.min_order))
        key = key_for(offer)
        if not reason and (key not in best or (offer.cost, offer.pk) < (best[key].cost, best[key].pk)):
            best[key] = offer
    listings = list(RetailListing.objects.select_related('offer', 'product').filter(
        Q(catalog_key__in=keys) | Q(catalog_key__isnull=True, offer__article__in=[r.article for r in rows])))
    existing = {key_for(item.offer): item for item in listings}
    categories = {o.category[:100] or 'Запчасти' for o in best.values()}
    Category.objects.bulk_create([Category(name=name) for name in categories if not Category.objects.filter(name=name).exists()], ignore_conflicts=True)
    category_ids = dict(Category.objects.filter(name__in=categories).values_list('name', 'pk'))
    new_products, new_listings, changed_products, changed_listings = [], [], [], []
    counts = Counter()
    for key in sorted(keys):
        offer = best.get(key)
        item = existing.get(key)
        if not offer:
            counts['unavailable'] += 1
            if item:
                item.product.status = 'hidden'; item.product.stock_qty = 0
                item.hold_reason = 'Нет актуального наличия у трёх поставщиков.'
                changed_products.append(item.product); changed_listings.append(item)
            continue
        if item is None:
            product = Product(article=offer.article, seller_profile=seller, seller_name=seller.name,
                whatsapp_number=seller.phone, city=seller.city or 'Алматы', supplier='procurement', condition='new',
                slug=(slugify(offer.name)[:170] or 'product') + f'-zpt-{offer.pk}')
            item = RetailListing(offer=offer, product=product, catalog_key=key,
                source_content={'title': offer.name or f'{offer.brand} {offer.article}',
                    'description': 'Новая автозапчасть. Совместимость уточняйте перед покупкой.'})
            new_products.append(product); new_listings.append(item)
            counts['created'] += 1
        else:
            product = item.product
            # Reusing an exact brand/article preserves the public URL and photographs.
            item.offer = offer; item.catalog_key = key
            changed_products.append(product); changed_listings.append(item)
            counts['updated'] += 1
        item.source_content = {**item.source_content, 'pricing_policy': POLICY}
        item.pack_size = max(1, offer.min_order)
        item.hold_reason = ''
        product.title = (item.source_content['title'] + (f' — упаковка {item.pack_size} шт.' if item.pack_size > 1 else ''))[:255]
        product.price = stock_price(offer, item.pack_size)[0]
        product.cost_price = int((offer.cost * item.pack_size).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        product.stock_qty = stock_quantity(offer.stock) // item.pack_size
        product.status = 'active' if item.enabled else 'hidden'
        product.price_on_request = False
        product.category_id = category_ids[offer.category[:100] or 'Запчасти']
        product.description = item.source_content['description'] + f'\n\nПроизводитель: {offer.brand or "уточняется"}. Артикул: {offer.article}.\nКоличество в цене: {item.pack_size} шт.\nНаличие по прайсу поставщика. Дата данных: {offer.supplier_checked_on.isoformat() if offer.supplier_checked_on else "не указана"}. Наличие подтверждаем перед оплатой.'
        product.updated_at = timezone.now()
        item.updated_at = timezone.now()
    Product.objects.bulk_create(new_products, batch_size=200)
    for item in new_listings:
        item.product_id = item.product.pk
    RetailListing.objects.bulk_create(new_listings, batch_size=200)
    if changed_products:
        Product.objects.bulk_update(changed_products, ['title', 'price', 'cost_price', 'stock_qty', 'status', 'price_on_request', 'category', 'description', 'updated_at'], batch_size=200)
        RetailListing.objects.bulk_update(changed_listings, ['offer', 'catalog_key', 'source_content', 'pack_size', 'hold_reason', 'updated_at'], batch_size=200)
    copy_vehicle_filters(new_listings + changed_listings)
    run.cursor = rows[-1].pk
    run.processed += len(rows)
    summary = Counter(run.summary)
    summary.update(counts)
    run.summary = dict(summary)
    run.status = 'done' if len(rows) < batch_size or run.cursor >= run.max_offer_id else 'running'
    run.save(update_fields=['cursor', 'processed', 'summary', 'status', 'updated_at'])
    if run.status == 'done':
        run.summary.pop('offer_ids', None)
        run.save(update_fields=['summary'])
        Activity.objects.create(user=run.user, action='retail_stock_done', details={'run': run.pk, **run.summary})
    return run
