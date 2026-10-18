"""Owner-approved activation. No queue building, resends or provider calls."""
import json
from pathlib import Path
from django.db import migrations

PAYLOAD=Path(__file__).resolve().parents[1]/'import_data'/'activate_imports_20261018.json'


def activate(apps, payload):
    Seller=apps.get_model('core','Seller')
    Brand=apps.get_model('core','Brand')
    Dispatch=apps.get_model('core','RequestDispatch')
    ServiceSeller=apps.get_model('service_requests','ServiceSeller')
    Service=apps.get_model('service_requests','Service')
    changed=0
    for plan in payload['plans']:
        seller=Seller.objects.select_for_update().get(pk=plan['id'])
        if seller.name != plan['before']['name'] or seller.whatsapp != plan['before']['whatsapp']:
            raise RuntimeError('Seller identity changed')
        already=all(getattr(seller,k)==v for k,v in plan['updates'].items())
        if not already and any(getattr(seller,k)!=v for k,v in plan['before'].items()):
            raise RuntimeError('Activation snapshot changed for seller %s' % seller.pk)
        if not seller.whatsapp or not seller.is_active or seller.is_paused or seller.is_test_seller:
            raise RuntimeError('Seller is unavailable; review required')
        if not already and Dispatch.objects.filter(seller_id=seller.pk,status='queued').exists():
            raise RuntimeError('Historical queue requires review before activation')
        if not already:
            Seller.objects.filter(pk=seller.pk).update(**plan['updates']);changed+=1
        for brand_id in plan['add_brands']:
            if not Brand.objects.filter(pk=brand_id).exists(): raise RuntimeError('Missing brand reference')
            seller.selected_brands.add(brand_id)
    target=payload['service']
    obj=ServiceSeller.objects.select_for_update().get(pk=target['id'],name=target['name'],whatsapp=target['whatsapp'],is_active=True,is_paused=False,is_test_seller=False)
    for key,name in target['services'].items():
        service=Service.objects.get(pk=int(key),name=name)
        obj.services.add(service.pk)
    ServiceSeller.objects.filter(pk=obj.pk).update(receive_requests=True)
    return changed


def load(apps,schema_editor):
    connection=schema_editor.connection
    if connection.vendor!='postgresql' or connection.settings_dict.get('NAME')!='zpt_kz':return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)',[202610180096])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='core' AND name='0096_activate_imported_organisations'")
        if cursor.fetchone():return
    print('ACTIVATE_IMPORTS_20261018 sellers changed:',activate(apps,json.loads(PAYLOAD.read_text())))


class Migration(migrations.Migration):
    dependencies=[('core','0095_import_almaty_parts_20261017'),('service_requests','0011_serviceseller_catalog_notes')]
    operations=[migrations.RunPython(load,migrations.RunPython.noop)]
