"""Owner-confirmed provenance; no stock entries or source files are altered."""
import hashlib
import unicodedata
from django.db import migrations


def confirm(apps,schema_editor):
    Item=apps.get_model('inventory','Item')
    Snapshot=apps.get_model('inventory','StockSnapshot')
    Goods=apps.get_model('inventory','GoodsDocument')
    pp1_ids=set(Snapshot.objects.filter(batch__kind='pp1').values_list('pk',flat=True))
    pp1_articles=set(Snapshot.objects.filter(batch__kind='pp1').values_list('sku',flat=True))
    for item in Item.objects.all().iterator():
        evidence=item.preparation or {}
        legacy=evidence.get('pp1_snapshot') in pp1_ids or (item.article in pp1_articles and item.code=='AG-'+item.article)
        if legacy:
            evidence['supplier_confirmation']={'source':'Owner instruction 2026-11-10: original PP1 assortment','previous_brand':item.brand,'supplier':'ТОО Азия Грейт','brand':'AG Parts'}
            Item.objects.filter(pk=item.pk).update(source_supplier='ТОО Азия Грейт',brand='AG Parts',preparation=evidence)
        elif item.brand.strip().casefold().replace('-','').replace(' ','')=='agparts':
            Item.objects.filter(pk=item.pk).update(brand='AG Parts')
    for doc in Goods.objects.filter(kind='transfer',evidence_only=True,warehouse='PP1',destination='PP2').iterator():
        if doc.counterparty not in ['PP1 → Rapida','Asia Grade','ТОО Азия Грейт']:continue
        parts=[doc.kind,doc.number,str(doc.date),'ТОО Азия Грейт',doc.warehouse,doc.destination]
        identity=hashlib.sha256('\0'.join(' '.join(unicodedata.normalize('NFKC',str(s)).casefold().split()) for s in parts).encode()).hexdigest()
        fields={'counterparty':'ТОО Азия Грейт'}
        if not Goods.objects.filter(identity=identity).exclude(pk=doc.pk).exists():fields['identity']=identity
        Goods.objects.filter(pk=doc.pk).update(**fields)


class Migration(migrations.Migration):
    dependencies=[('inventory','0014_item_source_supplier')]
    operations=[migrations.RunPython(confirm,migrations.RunPython.noop)]
