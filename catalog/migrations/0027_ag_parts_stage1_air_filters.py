from django.db import migrations
from django.db.models import Q
from django.utils.text import slugify

from catalog.ag_parts_air_filters import (
    AG_PARTS_SLUG,
    LEGACY_NULL_DUPLICATES,
    STAGE1_AIR_FILTERS,
)


def _canonical(Product, seller, article):
    return (
        Product.objects.filter(seller_profile_id=seller.pk, article=article)
        .order_by('id')
        .first()
    )


def _unique_slug(Product, title):
    base = slugify(title, allow_unicode=False) or 'product'
    slug = base
    counter = 1
    while Product.objects.filter(slug=slug).exists():
        counter += 1
        slug = f'{base}-{counter}'
    return slug


def _blocked_reason(product, apps):
    OrderItem = apps.get_model('orders', 'OrderItem')
    ProductConsignmentRequest = apps.get_model('catalog', 'ProductConsignmentRequest')
    if OrderItem.objects.filter(product_id=product.pk).exists():
        return 'has_order_item'
    if ProductConsignmentRequest.objects.filter(product_id=product.pk).exists():
        return 'has_consignment_request'
    return ''


def _remove_legacy_null(product, canonical, apps, ProductPriceTier):
    if canonical is None or product.pk == canonical.pk:
        return
    if product.seller_profile_id:
        return
    blocked = _blocked_reason(product, apps)
    ProductPriceTier.objects.filter(product_id=product.pk).delete()
    if blocked:
        product.status = 'hidden'
        product.publish_to_sellers = False
        product.save(update_fields=['status', 'publish_to_sellers'])
        return
    product.delete()


def _upsert_tier(ProductPriceTier, product, wholesale):
    ProductPriceTier.objects.filter(
        product_id=product.pk,
        is_active=True,
    ).exclude(min_qty=1).update(is_active=False)
    tier = (
        ProductPriceTier.objects.filter(product_id=product.pk, min_qty=1)
        .order_by('id')
        .first()
    )
    if tier is None:
        tier = ProductPriceTier.objects.create(
            product=product,
            min_qty=1,
            price=wholesale,
            is_active=True,
        )
    else:
        fields = []
        if tier.price != wholesale:
            tier.price = wholesale
            fields.append('price')
        if not tier.is_active:
            tier.is_active = True
            fields.append('is_active')
        if fields:
            tier.save(update_fields=fields)
    ProductPriceTier.objects.filter(
        product_id=product.pk,
        min_qty=1,
    ).exclude(pk=tier.pk).update(is_active=False)


def upsert_ag_parts_stage1_air_filters(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    Product = apps.get_model('catalog', 'Product')
    ProductPriceTier = apps.get_model('catalog', 'ProductPriceTier')

    seller = SellerProfile.objects.filter(slug=AG_PARTS_SLUG).first()
    if seller is None:
        return

    for article, (retail, wholesale) in STAGE1_AIR_FILTERS.items():
        product = _canonical(Product, seller, article)
        if product is None:
            title = f'Воздушный фильтр {article}'
            product = Product(
                title=title,
                article=article,
                slug=_unique_slug(Product, title),
                price=retail,
                seller_name=seller.name,
                seller_profile=seller,
                whatsapp_number=seller.phone or '',
                city=seller.city or '',
                status='active',
                condition='new',
                publish_to_sellers=True,
                publish_to_kaspi=False,
            )
            product.save()
        else:
            fields = []
            if product.price != retail:
                product.price = retail
                fields.append('price')
            if product.status != 'active':
                product.status = 'active'
                fields.append('status')
            if not product.publish_to_sellers:
                product.publish_to_sellers = True
                fields.append('publish_to_sellers')
            if product.seller_profile_id != seller.pk:
                product.seller_profile = seller
                fields.append('seller_profile')
            if fields:
                product.save(update_fields=fields)
        _upsert_tier(ProductPriceTier, product, wholesale)

    for spec in LEGACY_NULL_DUPLICATES:
        article = spec['article']
        canonical = _canonical(Product, seller, article)
        legacy_qs = Product.objects.filter(
            seller_profile__isnull=True,
            article=article,
        ).filter(Q(seller_name__iexact=seller.name) | Q(seller_name__iexact='AG Parts'))
        leftovers = list(legacy_qs)
        if spec.get('legacy_price') is not None:
            priced = [item for item in leftovers if item.price == spec['legacy_price']]
            if priced:
                leftovers = priced
        for leftover in leftovers:
            _remove_legacy_null(leftover, canonical, apps, ProductPriceTier)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0026_clean_ag_parts_wholesale_assortment'),
        ('orders', '0006_wholesalefunnelevent'),
    ]

    operations = [
        migrations.RunPython(
            upsert_ag_parts_stage1_air_filters,
            migrations.RunPython.noop,
        ),
    ]
