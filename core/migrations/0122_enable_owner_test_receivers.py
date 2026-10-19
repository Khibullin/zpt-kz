"""Owner approved 19 Oct 2026: all existing test contacts receive all NEW directions."""
import re
from django.db import migrations
from django.utils import timezone


def phone_key(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits if 10 <= len(digits) <= 15 else ''


def enable_owner_test_receivers(apps, schema_editor):
    Seller = apps.get_model('core', 'Seller')
    ServiceSeller = apps.get_model('service_requests', 'ServiceSeller')
    Service = apps.get_model('service_requests', 'Service')
    db = schema_editor.connection.alias
    now = timezone.now()
    parts = list(Seller.objects.using(db).filter(is_test_seller=True))
    services = list(ServiceSeller.objects.using(db).filter(is_test_seller=True))
    contacts = {}
    for row in parts + services:
        phone = phone_key(row.whatsapp)
        if not phone:
            raise RuntimeError('Test receiver has an invalid WhatsApp; fix before enabling: %s' % row.pk)
        contacts.setdefault(phone, row)
    # Companion profiles are needed because the two request systems have separate FKs.
    # Never promote an ordinary card even if its phone happens to match a test card.
    for phone, source in contacts.items():
        if not any(phone_key(s.whatsapp) == phone for s in parts):
            parts.append(Seller.objects.using(db).create(name=source.name, whatsapp=phone,
                transport_type='car', city=source.city, is_test_seller=True, catalog_only=True))
        if not any(phone_key(s.whatsapp) == phone for s in services):
            if any(phone_key(s.whatsapp) == phone for s in ServiceSeller.objects.using(db).all()):
                raise RuntimeError('A non-test service card already owns a test phone; explicit reconciliation required')
            services.append(ServiceSeller.objects.using(db).create(name=source.name, whatsapp=phone,
                seller_type='sto', city=source.city, password='', is_test_seller=True, catalog_only=True))
    common = dict(transport_types=['car', 'truck'], all_brands=True, all_categories=True, brands=[], categories=[])
    for seller in parts:
        Seller.objects.using(db).filter(pk=seller.pk, is_test_seller=True).update(
            is_active=True, is_paused=False, receive_requests=True,
            sells_new_parts=True, is_dismantler=True,
            all_brands=True, all_models=True, all_categories=True, all_countries=True,
            request_filters=common, request_filters_updated_at=now,
            test_all_requests_since=seller.test_all_requests_since or now)
    # Include the full existing service vocabulary; universal routing also accepts future services.
    all_services = list(Service.objects.using(db).values_list('pk', flat=True))
    for seller in services:
        ServiceSeller.objects.using(db).filter(pk=seller.pk, is_test_seller=True).update(
            is_active=True, is_paused=False, receive_requests=True,
            request_filters={**common, 'service_type': seller.seller_type}, request_filters_updated_at=now,
            test_all_requests_since=seller.test_all_requests_since or now)
        seller.services.set(all_services)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0121_seller_test_all_requests_since'),
        ('service_requests', '0015_serviceseller_test_all_requests_since'),
    ]
    operations = [migrations.RunPython(enable_owner_test_receivers, migrations.RunPython.noop)]
