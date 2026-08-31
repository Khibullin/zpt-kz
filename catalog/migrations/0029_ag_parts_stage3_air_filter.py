from django.db import migrations
from django.utils.text import slugify

from catalog.ag_parts_air_filters import AG_PARTS_SLUG, STAGE3_AIR_FILTERS


def _unique_slug(Product, title):
    base = slugify(title, allow_unicode=False) or 'product'
    slug = base
    counter = 1
    while Product.objects.filter(slug=slug).exists():
        counter += 1
        slug = f'{base}-{counter}'
    return slug


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


def _find_existing(Product, seller, article, preferred_pk):
    owned = list(
        Product.objects.filter(seller_profile_id=seller.pk, article=article)
        .order_by('id')[:2]
    )
    if owned:
        return owned[0]
    iexact_owned = list(
        Product.objects.filter(seller_profile_id=seller.pk, article__iexact=article)
        .order_by('id')[:2]
    )
    if iexact_owned:
        return iexact_owned[0]

    legacy = list(
        Product.objects.filter(seller_profile__isnull=True, article=article)
        .order_by('id')
    )
    if not legacy:
        legacy = list(
            Product.objects.filter(seller_profile__isnull=True, article__iexact=article)
            .order_by('id')
        )
    if not legacy:
        return None
    if preferred_pk:
        for product in legacy:
            if product.pk == preferred_pk:
                return product
    return legacy[0]


def upsert_ag_parts_stage3_air_filter(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    Product = apps.get_model('catalog', 'Product')
    ProductPriceTier = apps.get_model('catalog', 'ProductPriceTier')

    seller = SellerProfile.objects.filter(slug=AG_PARTS_SLUG).first()
    if seller is None:
        return

    for article, spec in STAGE3_AIR_FILTERS.items():
        retail = spec['retail']
        wholesale = spec['wholesale']
        title = spec['title']
        preferred_pk = spec.get('preferred_legacy_pk')
        product = _find_existing(Product, seller, article, preferred_pk)
        if product is None:
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
            if product.article != article:
                product.article = article
                fields.append('article')
            if product.title != title:
                product.title = title
                fields.append('title')
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
            if (product.seller_name or '').strip() != seller.name:
                product.seller_name = seller.name
                fields.append('seller_name')
            if fields:
                product.save(update_fields=fields)
        _upsert_tier(ProductPriceTier, product, wholesale)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0028_ag_parts_stage2_air_filters'),
    ]

    operations = [
        migrations.RunPython(
            upsert_ag_parts_stage3_air_filter,
            migrations.RunPython.noop,
        ),
    ]
