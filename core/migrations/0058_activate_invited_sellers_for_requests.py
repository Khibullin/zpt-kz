from django.db import migrations
from django.utils import timezone


PROFILES = {
    2: {'transport': 'car', 'mode': 'universal'},
    3: {'transport': 'car', 'mode': 'brands', 'brands': ('BMW',)},
    11: {'transport': 'car', 'mode': 'brands', 'brands': ('Audi', 'Volkswagen')},
    18: {'transport': 'car', 'mode': 'universal'},
    21: {'transport': 'car', 'mode': 'brands', 'brands': ('Mercedes-Benz',), 'city': 'Шымкент'},
    27: {'transport': 'car', 'mode': 'china'},
    30: {'transport': 'car', 'mode': 'brands', 'brands': ('Toyota', 'Lexus')},
    45: {'transport': 'car', 'mode': 'brands', 'brands': ('Mercedes-Benz',)},
    51: {'transport': 'car', 'mode': 'china'},
    63: {'transport': 'car', 'mode': 'universal', 'city': 'Астана'},
    69: {'transport': 'car', 'mode': 'brands', 'brands': ('Mercedes-Benz',)},
    72: {'transport': 'truck', 'mode': 'universal'},
    97: {'transport': 'car', 'mode': 'brands', 'brands': ('Kia', 'Hyundai'), 'city': 'Актау'},
    98: {'transport': 'car', 'mode': 'brands', 'brands': ('Audi',)},
    108: {'transport': 'car', 'mode': 'brands', 'brands': ('Hyundai',)},
    115: {'transport': 'car', 'mode': 'universal'},
    121: {'transport': 'car', 'mode': 'universal'},
}


def _norm_phone(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    return digits


def _find_seller_by_phone(Seller, phone):
    target = _norm_phone(phone)
    if not target:
        return None
    for seller in Seller.objects.all().iterator():
        if _norm_phone(seller.whatsapp) == target:
            return seller
    return None


def activate_invited_sellers(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')
    Seller = apps.get_model('core', 'Seller')
    Brand = apps.get_model('core', 'Brand')
    Country = apps.get_model('core', 'Country')

    now = timezone.now()
    china = Country.objects.filter(name='Китай').first()
    if china is None:
        raise RuntimeError('Country "Китай" is required for seller activation.')

    required_brand_names = {
        brand
        for profile in PROFILES.values()
        for brand in profile.get('brands', ())
    }
    available_brand_names = set(
        Brand.objects.filter(
            name__in=required_brand_names,
            transport_type='car',
        ).values_list('name', flat=True)
    )
    missing_brand_names = sorted(required_brand_names - available_brand_names)
    if missing_brand_names:
        raise RuntimeError(
            'Missing seller activation brands: ' + ', '.join(missing_brand_names)
        )

    chinese_brands = list(
        Brand.objects.filter(
            country=china,
            transport_type='car',
        ).order_by('id')
    ) if china else []

    for lead_id, profile in PROFILES.items():
        lead = SellerLead.objects.filter(
            pk=lead_id,
            duplicate_of__isnull=True,
            lifecycle_status='invited',
        ).first()
        if lead is None:
            continue

        whatsapp = _norm_phone(lead.whatsapp)
        if not whatsapp:
            continue

        if lead.request_seller_id:
            seller = Seller.objects.filter(pk=lead.request_seller_id).first()
            if seller is None:
                continue
            other = _find_seller_by_phone(Seller, whatsapp)
            if other is not None and other.pk != seller.pk:
                continue
        else:
            seller = _find_seller_by_phone(Seller, whatsapp)
            if seller is None:
                seller = Seller.objects.create(
                    name=(lead.name or '')[:255],
                    whatsapp=whatsapp[:20],
                    city=(profile.get('city') or lead.city or '')[:100],
                    transport_type=profile['transport'],
                    notes=f'SellerLead #{lead.pk}',
                    receive_requests=False,
                    is_active=True,
                    is_paused=False,
                )
            lead.request_seller_id = seller.pk

        city = profile.get('city') or lead.city or seller.city or ''
        seller.name = (lead.name or seller.name)[:255]
        seller.whatsapp = whatsapp[:20]
        seller.city = city[:100]
        seller.transport_type = profile['transport']
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
        elif mode == 'china':
            seller.all_countries = False
            seller.all_brands = False
        elif mode == 'brands':
            seller.all_countries = True
            seller.all_brands = False

        seller.save(update_fields=[
            'name',
            'whatsapp',
            'city',
            'transport_type',
            'is_active',
            'is_paused',
            'receive_requests',
            'all_categories',
            'all_countries',
            'all_brands',
            'all_models',
            'category',
            'brand',
            'model',
            'country_fk',
            'brand_fk',
            'model_fk',
        ])

        seller.selected_categories.clear()
        seller.selected_countries.clear()
        seller.selected_brands.clear()
        seller.selected_models.clear()

        if mode == 'china':
            seller.selected_countries.add(china)
            seller.selected_brands.add(*chinese_brands)
        elif mode == 'brands':
            names = tuple(profile.get('brands') or ())
            brands = list(
                Brand.objects.filter(
                    name__in=names,
                    transport_type=profile['transport'],
                )
            )
            if len(brands) == len(set(names)):
                seller.selected_brands.add(*brands)

        lead.request_seller_transport_type = profile['transport']
        if profile.get('city'):
            lead.city = city[:100]
        lead.reviewed_at = lead.reviewed_at or now
        lead.review_status = (
            'converted_and_marketplace_planned'
            if lead.marketplace_invitation_status == 'planned'
            else 'converted_requests'
        )
        lead.updated_at = now
        lead.save(update_fields=[
            'request_seller',
            'request_seller_transport_type',
            'city',
            'reviewed_at',
            'review_status',
            'updated_at',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0057_correct_invalid_invited_whatsapp'),
    ]

    operations = [
        migrations.RunPython(
            activate_invited_sellers,
            migrations.RunPython.noop,
        ),
    ]
