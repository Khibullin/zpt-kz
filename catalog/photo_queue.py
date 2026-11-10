"""Owner-requested full catalogue photo queue; no price or stock writes."""
from django.apps import apps as live_apps
from django.db.models import Q

BATCH='full-catalogue-20261110'


def stage_all_photos(apps=None):
    apps=apps or live_apps
    Offer=apps.get_model('procurement','Offer')
    Listing=apps.get_model('procurement','RetailListing')
    Task=apps.get_model('catalog','SupplierPhotoTask')
    sources={}
    for row in Offer.objects.filter(supplier_url__startswith='https://omega-auto-parts.kz/').order_by('pk').values('pk','brand','article','supplier_url').iterator(chunk_size=1000):
        key=(row['brand'].strip().upper(),row['article'].strip().upper())
        sources.setdefault(key,(row['pk'],row['supplier_url']))
    existing={row['product_id']:row for row in Task.objects.values('pk','product_id','status','note','evidence')}
    counts={'prepared':0,'needs_source':0,'existing':0}
    pending=[]
    rows=Listing.objects.filter(enabled=True,product__status='active').filter(
        Q(product__main_image='')|Q(product__main_image__isnull=True)
    ).select_related('offer').order_by('pk')
    for row in rows.iterator(chunk_size=1000):
        offer=row.offer
        source=sources.get((offer.brand.strip().upper(),offer.article.strip().upper()))
        status='prepared' if source else 'needs_source'
        old=existing.get(row.product_id)
        note='' if source else 'Нужен подтверждённый источник фото по производителю и артикулу.'
        if old:
            if old['status'] in ('pending','prepared','blocked') or (
                old['evidence'].get('batch')==BATCH and old['status']!='needs_source'
            ) or (old['status']=='needs_source' and not source):
                counts['existing']+=1
                continue
            Task.objects.filter(pk=old['pk']).update(status=status,
                offer_id=source[0] if source else offer.pk,
                source_url=source[1] if source else '',note=note,
                evidence={**old['evidence'],'batch':BATCH})
        else:
            pending.append(Task(product_id=row.product_id,
                offer_id=source[0] if source else offer.pk,
                source_url=source[1] if source else '',status=status,note=note,
                evidence={'batch':BATCH}))
            if len(pending)>=1000:
                Task.objects.bulk_create(pending,ignore_conflicts=True,batch_size=500)
                pending=[]
        counts[status]+=1
    if pending:Task.objects.bulk_create(pending,ignore_conflicts=True,batch_size=500)
    return counts
