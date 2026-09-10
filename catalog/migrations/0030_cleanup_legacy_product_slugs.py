from django.db import migrations


PRODUCT_SLUG_CHANGES = (
    (1981, 'audi', 'peugeot-308-hu71151x'),
    (1988, 'chery', 'chery-tiggo-7-t151109111'),
    (1989, 'byd', 'byd-tang-13033898-00'),
    (1990, 'byd-2', 'byd-em2e-8121211e'),
    (1991, 'changan', 'changan-uni-k-1109190cr01'),
    (1992, 'changan-2', 'changan-cs35-s1010140400'),
    (1993, 'changan-3', 'changan-uni-v-c281f2801032601'),
    (1995, 'changan-4', 'changan-s111f2801031700'),
    (2013, 'prado-95', 'prado-95-pnh95'),
    (2045, 'chevrolet', 'chevrolet-brake-pads-2045'),
    (2047, 'jac', 'great-wall-poer-1017110xed95'),
    (2082, 'nissan', 'nissan-304887'),
    (2083, 'jeep', 'jeep-103373'),
    (2084, 'jeep-2', 'jeep-crab-043'),
    (2087, 'jeep-3', 'jeep-crab-056'),
    (2088, 'product', 'japanparts-pp-998af'),
    (2090, 'jeep-4', 'jeep-0201-ja60r'),
    (2091, 'moog', 'moog-k80604'),
    (2096, 'jeep-5', 'jeep-2025-libr'),
    (2100, 'jeep-6', 'jeep-52124302ac'),
    (2102, 'jeep-7', 'jeep-5143700aa'),
    (2145, 'hyundai', 'hyundai-oe18846-11070'),
)


def migrate_slugs(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')

    for pk, old_slug, new_slug in PRODUCT_SLUG_CHANGES:
        product = Product.objects.filter(pk=pk).first()
        if product is None or product.slug == new_slug:
            continue
        if product.slug != old_slug:
            continue
        if Product.objects.exclude(pk=pk).filter(slug=new_slug).exists():
            raise RuntimeError(f'Cannot migrate product {pk}: slug {new_slug!r} is already used')
        Product.objects.filter(pk=pk).update(slug=new_slug)


def reverse_slugs(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')

    for pk, old_slug, new_slug in reversed(PRODUCT_SLUG_CHANGES):
        product = Product.objects.filter(pk=pk).first()
        if product is None or product.slug == old_slug:
            continue
        if product.slug != new_slug:
            continue
        if Product.objects.exclude(pk=pk).filter(slug=old_slug).exists():
            raise RuntimeError(f'Cannot restore product {pk}: slug {old_slug!r} is already used')
        Product.objects.filter(pk=pk).update(slug=old_slug)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0029_ag_parts_stage3_air_filter'),
    ]

    operations = [
        migrations.RunPython(migrate_slugs, reverse_slugs),
    ]
