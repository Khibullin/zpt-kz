from django.db import migrations
from django.utils import timezone


PROFILES = {
    4: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    37: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    44: {'transport': 'car', 'mode': 'brands', 'brands': ('BMW',), 'seller_type': 'both'},
    47: {'transport': 'car', 'mode': 'brands', 'brands': ('Hyundai', 'Kia'), 'seller_type': 'seller'},
    57: {'transport': 'car', 'mode': 'brands', 'brands': ('Hyundai', 'Kia'), 'seller_type': 'seller'},
    60: {'transport': 'car', 'mode': 'brands', 'brands': ('Audi', 'Volkswagen'), 'seller_type': 'seller'},
    113: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    114: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    116: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    118: {'transport': 'car', 'mode': 'universal', 'seller_type': 'seller'},
    125: {'transport': 'car', 'mode': 'china', 'seller_type': 'seller'},
}


def _norm_phone(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    return digits


def _find_seller_by_phone(Seller, phone):
    target = _norm_phone(phone)
    if not target:
        return None
    for seller in Seller.objects.all().iterator():
        if _norm_phone(seller.whatsapp) == target:
            return seller
    return None


def activate_second_batch(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    Seller = apps.get_model('core', 'Seller')
    Brand = apps.get_model('core', 'Brand')
    Country = apps.get_model('core', 'Country')

    now = timezone.now()

    for lead_id, profile in PROFILES.items():
        lead = SellerLead.objects.filter(
            pk=lead_id,
            duplicate_of__isnull=True,
            lifecycle_status='ready_to_invite',
            whatsapp_confidence='high',
            market_scope='kz',
        ).first()
        if lead is None:
            continue

        whatsapp = _norm_phone(lead.whatsapp)
        if not whatsapp:
            continue

        existing = _find_seller_by_phone(Seller, whatsapp)
        if lead.request_seller_id:
            seller = Seller.objects.filter(pk=lead.request_seller_id).first()
            if seller is None:
                continue
            if existing is not None and existing.pk != seller.pk:
                continue
        elif existing is not None:
            seller = existing
            lead.request_seller_id = seller.pk
        else:
            seller = Seller.objects.create(
                name=(lead.name or '')[:255],
                whatsapp=whatsapp[:20],
                city=(lead.city or '')[:100],
                transport_type=profile['transport'],
                seller_type=profile['seller_type'],
                notes=f'SellerLead #{lead.pk}; direct request activation 09.10.2026',
                receive_requests=False,
                is_active=True,
                is_paused=False,
            )
            lead.request_seller_id = seller.pk

        # Preserve a useful existing seller name; generic discovery titles are
        # used only when a Seller is newly created or has no name.
        if not (seller.name or '').strip():
            seller.name = (lead.name or '')[:255]
        seller.whatsapp = whatsapp[:20]
        seller.city = (lead.city or seller.city or '')[:100]
        seller.transport_type = profile['transport']
        seller.seller_type = profile['seller_type']
        seller.is_active = True
        seller.is_paused = False
        seller.receive_requests = True
        seller.all_categories = True
        seller.all_models = True
        seller.category = ''
        seller.brand = ''
        seller.model = ''
        seller.country_fk_id = None
        seller.brand_fk_id = None
        seller.model_fk_id = None

        mode = profile['mode']
        if mode == 'universal':
            seller.all_countries = True
            seller.all_brands = True
        elif mode == 'brands':
            seller.all_countries = True
            seller.all_brands = False
        elif mode == 'china':
            seller.all_countries = False
            seller.all_brands = False
        else:
            raise RuntimeError(f'Unknown activation mode: {mode}')

        seller.save(update_fields=[
            'name', 'whatsapp', 'city', 'transport_type', 'seller_type',
            'is_active', 'is_paused', 'receive_requests',
            'all_categories', 'all_countries', 'all_brands', 'all_models',
            'category', 'brand', 'model',
            'country_fk', 'brand_fk', 'model_fk',
        ])

        seller.selected_categories.clear()
        seller.selected_countries.clear()
        seller.selected_brands.clear()
        seller.selected_models.clear()

        if mode == 'brands':
            names = tuple(profile.get('brands') or ())
            brands = list(
                Brand.objects.filter(
                    name__in=names,
                    transport_type=profile['transport'],
                )
            )
            if len(brands) != len(set(names)):
                raise RuntimeError(f'Brand profile mismatch for SellerLead #{lead.pk}')
            seller.selected_brands.add(*brands)
        elif mode == 'china':
            china, _ = Country.objects.get_or_create(name='Китай')
            chinese_brands = list(
                Brand.objects.filter(
                    country=china,
                    transport_type=profile['transport'],
                ).order_by('id')
            )
            seller.selected_countries.add(china)
            if chinese_brands:
                seller.selected_brands.add(*chinese_brands)
            else:
                seller.all_brands = True
                seller.save(update_fields=['all_brands'])

        lead.request_seller_transport_type = profile['transport']
        lead.reviewed_at = lead.reviewed_at or now
        lead.review_status = 'converted_requests'
        lead.lifecycle_status = 'active'
        lead.updated_at = now
        lead.save(update_fields=[
            'request_seller',
            'request_seller_transport_type',
            'reviewed_at',
            'review_status',
            'lifecycle_status',
            'updated_at',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_prepare_second_seller_invite_batch'),
    ]

    operations = [
        migrations.RunPython(activate_second_batch, migrations.RunPython.noop),
    ]
