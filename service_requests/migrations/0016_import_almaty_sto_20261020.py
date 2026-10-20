"""Owner-approved additive STO import. Never sends or queues messages."""
import json
import re
from pathlib import Path
from django.db import migrations
from django.contrib.auth.hashers import make_password

MARKER = 'ALMATY_STO_20261020'

def norm(value):
    return re.sub(r'[^\w]', '', (value or '').casefold())

def apply_batch(apps, rows):
    Seller = apps.get_model('service_requests', 'ServiceSeller')
    Service = apps.get_model('service_requests', 'Service')
    counts = dict(created=0, existing=0, connected=0, deferred=0, ambiguous=0, protected=0)
    issues=[]
    for row in rows:
        all_sellers=list(Seller.objects.select_for_update().all())
        exact=[s for s in all_sellers if re.search(r'(?<!\d)'+row['external_id']+r'(?!\d)',s.map_link or '')]
        phone_matches=[s for s in all_sellers if s.whatsapp in set(row['phones']+row['wa'])]
        address_matches=[s for s in all_sellers if row['address'] and norm(s.address)==norm(row['address']) and norm(s.name.split(',')[0])==norm(row['name'].split(',')[0])]
        matches={s.pk:s for s in (exact or phone_matches or address_matches)}
        if len(matches)>1:
            counts['ambiguous']+=1;issues.append([row['external_id'],'multiple_existing']);continue
        seller=next(iter(matches.values()),None)
        if seller and (seller.is_test_seller or seller.seller_type!='sto' or seller.is_paused or not seller.is_active):
            counts['protected']+=1;issues.append([row['external_id'],'protected_existing']);continue
        # Shared switchboards must not collapse distinct branches.
        if seller and not exact and phone_matches and seller.map_link and row['external_id'] not in seller.map_link:
            counts['ambiguous']+=1;issues.append([row['external_id'],'shared_phone']);continue
        if seller and not exact and phone_matches and norm(seller.name.split(',')[0])!=norm(row['name'].split(',')[0]):
            counts['ambiguous']+=1;issues.append([row['external_id'],'phone_name_conflict']);continue
        if seller is None:
            available=next((p for p in row['wa']+row['phones'] if not Seller.objects.filter(whatsapp=p).exists()),'')
            # Unique non-dialable key: source IDs contain 16–17 digits and fail phone validation.
            contact=available or 'src'+row['external_id']
            seller=Seller.objects.create(name=row['name'],whatsapp=contact,password=make_password(None),city=row['city'],seller_type='sto',catalog_only=True,is_active=True,is_test_seller=False,is_paused=False,receive_requests=False)
            counts['created']+=1
            new=True
        else:
            counts['existing']+=1;new=False
        values={}
        for field in ('address','description','website','instagram','map_link'):
            if not getattr(seller,field) and row[field]: values[field]=row[field]
        if exact and not seller.map_link.startswith(('https://','http://')):values['map_link']=row['map_link']
        if row['city'] and (new or exact): values['city']=row['city']
        if not seller.directory_transport:values['directory_transport']=row['transport']
        note=MARKER+' '+row['external_id']+'\n'+row['notes']
        if note not in seller.catalog_notes:values['catalog_notes']=(seller.catalog_notes+'\n'+note).strip()
        # Preserve confirmed filters and existing service choices.
        if seller.request_filters is None and (new or not seller.services.exists()):
            for name in row['services']:
                service=Service.objects.filter(name=name).first()
                if service: seller.services.add(service)
        city=values.get('city',seller.city)
        eligible=bool(row['city'] and city and seller.whatsapp in row['wa'] and seller.services.exists())
        if eligible: values['receive_requests']=True
        if values: Seller.objects.filter(pk=seller.pk).update(**values)
        seller.refresh_from_db()
        if seller.receive_requests:counts['connected']+=1
        else:counts['deferred']+=1
    print(MARKER,json.dumps({'counts':counts,'issues':issues},ensure_ascii=False))
    return counts,issues

def load(apps,schema_editor):
    c=schema_editor.connection
    if c.vendor!='postgresql' or c.settings_dict.get('NAME')!='zpt_kz':return
    with c.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)',[202610200016])
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='service_requests' AND name='0016_import_almaty_sto_20261020'")
        if cursor.fetchone():return
    rows=json.loads((Path(__file__).resolve().parents[1]/'import_data/almaty_sto_20261020.json').read_text())
    apply_batch(apps,rows)

class Migration(migrations.Migration):
    dependencies=[('service_requests','0015_serviceseller_test_all_requests_since')]
    operations=[migrations.RunPython(load,migrations.RunPython.noop)]
