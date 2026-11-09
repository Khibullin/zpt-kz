"""Publish the verified profitable selection without remote requests or private text."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from django.db import transaction
from django.utils import timezone

from catalog.models import Product, SellerProfile, Category, Brand, CarModel, Country, ProductFitment
from .analysis import evaluate
from .catalog_paging import best_candidates_query
from .models import Offer, RetailListing, Preferences, Activity
from .retail import stock_quantity, refresh_listing

POLICY = 'kaspi_minus_5'


def retail_price(offer, pack, policy):
    today = timezone.localdate()
    fresh = today - timedelta(days=7)
    def hold(reason):
        return 0, reason
    if not offer.match_verified or not offer.kaspi_url:
        return hold('Совпадение с Kaspi не подтверждено.')
    snapshot = getattr(offer, 'kaspi_sellers', None)
    if not (snapshot and snapshot.complete and snapshot.city == '750000000'
            and snapshot.source_url == offer.kaspi_url and fresh <= snapshot.checked_on <= today
            and offer.kaspi_price_checked_on and fresh <= offer.kaspi_price_checked_on <= today):
        return hold('Нужна актуальная полная проверка цены и продавцов Kaspi в Алматы.')
    try:
        prices = [Decimal(str(row['price'])) for row in snapshot.sellers]
        if not prices or any(not price.is_finite() or price <= 0 for price in prices):
            return hold('Нет подтверждённой цены продавцов Kaspi.')
        expenses = Decimal(str(policy['expenses']))
        if not expenses.is_finite() or expenses < 250:
            return hold('Расходы требуют уточнения.')
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return hold('Цена или расходы требуют уточнения.')
    # Round down: the published price must never exceed Kaspi minus 5%.
    unit_price = (min(prices) * Decimal('.95')).quantize(Decimal('1'), rounding=ROUND_FLOOR)
    if offer.cost <= 0 or unit_price - offer.cost - expenses < 1000:
        return hold('После скидки и расходов прибыль меньше 1 000 ₸ на единицу.')
    if not offer.supplier_checked_on or not fresh <= offer.supplier_checked_on <= today:
        return hold('Цена и наличие поставщика требуют обновления.')
    quantity = stock_quantity(offer.stock)
    if quantity is None or quantity < pack:
        return hold('Нет подтверждённого остатка для минимальной партии.')
    return int(unit_price * pack), ''


def publication_candidates(user):
    preferences = Preferences.objects.get(user=user)
    query = Offer.objects.filter(supplier__name__in=['Omega', 'Фаетон', 'ZakazAuto — Костанай'])
    query = best_candidates_query(query, preferences, timezone.localdate())
    result = []
    for offer in query.select_related('supplier', 'kaspi_sellers').prefetch_related('fitments').order_by('cost', 'pk'):
        evaluation = evaluate(offer, preferences)
        # These are the same demand conditions as the final profitable selection.
        if not evaluation['confirmed_sales'] or evaluation['stock_status'] != 'in_stock':
            continue
        policy = {'pricing_policy': POLICY, 'expenses': str(max(Decimal(250), preferences.expenses))}
        price, reason = retail_price(offer, max(1, offer.min_order), policy)
        if not reason:
            result.append((offer, price, policy))
    return result


@transaction.atomic
def publish_candidate(offer_id, user):
    # Serialize different suppliers' copies of the same brand/article via seller lock.
    seller = SellerProfile.objects.select_for_update().get(slug='ag-parts')
    offer = Offer.objects.select_for_update().select_related('supplier', 'kaspi_sellers').get(pk=offer_id)
    preferences = Preferences.objects.get(user=user)
    evaluation = evaluate(offer, preferences)
    if not evaluation['confirmed_sales'] or evaluation['profit'] is None or evaluation['profit'] < max(Decimal(1000), preferences.best_min_profit):
        return 'skipped'
    policy = {'pricing_policy': POLICY, 'expenses': str(max(Decimal(250), preferences.expenses))}
    _, reason = retail_price(offer, max(1, offer.min_order), policy)
    if reason:
        return 'skipped'
    listing = RetailListing.objects.select_related('offer', 'product').filter(
        product__seller_profile=seller, offer__brand__iexact=offer.brand, offer__article__iexact=offer.article).first()
    if listing and listing.offer_id != offer.pk:
        # One public card, one supplying offer. Never overwrite photos/content from a different source.
        return 'duplicate'
    if listing is None:
        if Product.objects.filter(seller_profile=seller, article__iexact=offer.article).exists():
            return 'duplicate'
        category, _ = Category.objects.get_or_create(name=offer.category[:100] or 'Запчасти')
        product = Product.objects.create(title=offer.name[:255], article=offer.article, seller_profile=seller,
            seller_name=seller.name, whatsapp_number=seller.phone, city=seller.city or 'Алматы',
            supplier='procurement', status='hidden', condition='new', category=category)
        # Never copy raw supplier descriptions or characteristics: they can contain private prices.
        listing = RetailListing.objects.create(offer=offer, product=product, source_content={
            'title': offer.name, 'description': 'Новая автозапчасть. Совместимость уточняйте перед покупкой.',
            'compatibility': '', **policy})
    else:
        listing.source_content = {**listing.source_content, **policy}
        listing.save(update_fields=['source_content', 'updated_at'])
    refresh_listing(listing)
    country, _ = Country.objects.get_or_create(name='Прочее')
    for fit in offer.fitments.filter(verified=True):
        brand = Brand.objects.filter(name__iexact=fit.make).first()
        if brand is None:
            brand = Brand.objects.create(name=fit.make[:100], country=country)
        model, _ = CarModel.objects.get_or_create(brand=brand, name=fit.model[:100])
        listing.product.selected_brands.add(brand)
        listing.product.selected_models.add(model)
        ProductFitment.objects.get_or_create(product=listing.product, car_model=model, source_url=fit.source_url,
            defaults={'status': 'confirmed', 'engine_policy': 'unknown', 'verified_on': timezone.localdate(),
                      'note': 'Проверенная применимость из базы поставщика. Перед покупкой сверить модификацию.'})
    Activity.objects.create(user=user, action='retail_publish', details={
        'offer': offer.pk, 'product': listing.product_id, 'policy': POLICY, 'price': listing.product.price})
    return 'published'
