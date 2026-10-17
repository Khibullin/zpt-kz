"""Confirm three existing sellers from live products, without paid lookups.

Evidence checked on 2026-10-17:
* GRM4x4 — 43 active products explicitly marked new.
* KOREA-PARTS — 12 active products explicitly marked new.
* AUTOLEADER — 3 active products explicitly marked new.

Each business was matched by both its seller name and a unique normalized
WhatsApp number, not by name or location alone.
No other users, contacts, imported dismantlers or statuses are changed.
"""
import re

from django.db import migrations


CONFIRMED = (
    (302, '"GRM4x4"', 'grm4x4'),
    (414, 'KOREA-PARTS', 'KOREA-PARTS'),
    (415, 'AUTOLEADER', 'AUTOLEADER'),
)


def _phone_suffix(value):
    digits = re.sub(r'[^0-9]', '', str(value or ''))
    return digits[-10:] if len(digits) >= 10 else ''


def verify_existing_new_parts_by_catalog(apps, schema_editor):
    Seller = apps.get_model('core', 'Seller')
    Product = apps.get_model('catalog', 'Product')
    for seller_id, expected_name, product_seller_name in CONFIRMED:
        seller = Seller.objects.filter(
            pk=seller_id,
            name=expected_name,
            sells_new_parts__isnull=True,
        ).first()
        if seller is None:
            continue

        suffix = _phone_suffix(seller.whatsapp)
        if not suffix:
            continue

        # Do not trust phones shared by two Seller profiles.
        matching_seller_count = sum(
            1 for number in Seller.objects.values_list('whatsapp', flat=True)
            if _phone_suffix(number) == suffix
        )
        if matching_seller_count != 1:
            continue

        published = Product.objects.filter(
            seller_name__iexact=product_seller_name,
            status='active',
            condition='new',
        ).values_list('whatsapp_number', flat=True)
        if any(_phone_suffix(phone) == suffix for phone in published):
            Seller.objects.filter(
                pk=seller.pk, name=expected_name, sells_new_parts__isnull=True,
            ).update(sells_new_parts=True)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0089_correct_existing_seller_specializations'),
        ('catalog', '0073_apply_fitment_audit_20261012'),
    ]

    operations = [
        migrations.RunPython(
            verify_existing_new_parts_by_catalog,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
