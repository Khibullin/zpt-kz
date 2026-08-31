from django.db import migrations
from django.utils.text import slugify

from catalog.ag_parts_air_filters import AG_PARTS_SLUG, STAGE2_AIR_FILTERS
from catalog.applicability import serialize_plain_list


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


def upsert_ag_parts_stage2_air_filters(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    Product = apps.get_model('catalog', 'Product')
    ProductPriceTier = apps.get_model('catalog', 'ProductPriceTier')

    seller = SellerProfile.objects.filter(slug=AG_PARTS_SLUG).first()
    if seller is None:
        return

    for article, spec in STAGE2_AIR_FILTERS.items():
        retail = spec['retail']
        wholesale = spec['wholesale']
        title = spec['title']
        compatibility = spec.get('compatibility') or ''
        oem = serialize_plain_list(spec.get('oem_cross_references') or '')
        product = _canonical(Product, seller, article)
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
                compatibility=compatibility,
                oem_cross_references=oem,
            )
            product.save()
        else:
            fields = []
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
            if compatibility and product.compatibility != compatibility:
                product.compatibility = compatibility
                fields.append('compatibility')
            if oem and product.oem_cross_references != oem:
                product.oem_cross_references = oem
                fields.append('oem_cross_references')
            if fields:
                product.save(update_fields=fields)
        _upsert_tier(ProductPriceTier, product, wholesale)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0027_ag_parts_stage1_air_filters'),
    ]

    operations = [
        migrations.RunPython(
            upsert_ag_parts_stage2_air_filters,
            migrations.RunPython.noop,
        ),
    ]
