from django.db import migrations


def confirm_new_parts_from_published_catalog(apps, schema_editor):
    """Existing active goods explicitly marked 'new' confirm this specialization.

    An unrelated seller without such published goods stays unclassified.
    """
    Seller = apps.get_model('core', 'Seller')
    Product = apps.get_model('catalog', 'Product')
    user_ids = Product.objects.filter(
        seller_profile__user_id__isnull=False,
        status='active',
        condition='new',
    ).values_list('seller_profile__user_id', flat=True).distinct()
    Seller.objects.filter(
        user_id__in=user_ids,
        sells_new_parts__isnull=True,
    ).update(sells_new_parts=True)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0087_seller_sells_new_parts'),
        ('catalog', '0073_apply_fitment_audit_20261012'),
    ]

    operations = [
        migrations.RunPython(
            confirm_new_parts_from_published_catalog,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
