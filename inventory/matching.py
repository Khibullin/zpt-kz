"""Candidate matching only. An article match is not confirmation of package size."""
import re
from collections import defaultdict
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import ImportBatch, StockSnapshot, Item, ItemAlias, RapidaReference

def normalized(value):
    return re.sub(r'[^A-Z0-9А-ЯЁ]','',str(value).upper())

def candidate_articles(sku, name, articles):
    tokens = re.findall(r'[A-Za-zА-Яа-яЁё0-9]+(?:[-_/][A-Za-zА-Яа-яЁё0-9]+)*',name or '')
    keys = {normalized(sku)} | {normalized(t) for t in tokens}
    return sorted(a for a in articles if normalized(a) in keys)

def reconciliation_rows():
    pp1=ImportBatch.objects.filter(kind='pp1').order_by('-as_of','-pk').first()
    pp2=ImportBatch.objects.filter(kind='stock').order_by('-as_of','-pk').first()
    articles=list(StockSnapshot.objects.filter(batch=pp1).values_list('sku',flat=True)) if pp1 else []
    mapped={(a.source,a.code):a for a in ItemAlias.objects.select_related('item')}
    refs=defaultdict(list)
    for r in RapidaReference.objects.select_related('batch').order_by('-batch__as_of','-batch_id','pk'):
        refs[normalized(r.article)].append(r)
    result=[]
    for snapshot in StockSnapshot.objects.filter(batch_id__in=[b.pk for b in [pp1,pp2] if b]).order_by('warehouse','sku'):
        source='pp1' if snapshot.warehouse=='PP1' else 'kaspi'
        candidates=[snapshot.sku] if source=='pp1' else candidate_articles(snapshot.sku,snapshot.name,articles)
        result.append({'snapshot':snapshot,'source':source,'candidates':candidates,'alias':mapped.get((source,snapshot.sku)), 'references':refs.get(normalized(candidates[0]),[]) if len(candidates)==1 else []})
    groups=defaultdict(list)
    for row in result:
        if row['source']=='kaspi' and len(row['candidates'])==1:groups[row['candidates'][0]].append(row)
    for rows in groups.values():
        for row in rows:row['shared_count']=len(rows)
    return result

@transaction.atomic
def confirm_mapping(snapshot_id, item, multiplier, user):
    snapshot=StockSnapshot.objects.select_for_update().get(pk=snapshot_id)
    latest=ImportBatch.objects.filter(kind=snapshot.batch.kind).order_by('-as_of','-pk').first()
    if not latest or latest.pk!=snapshot.batch_id:raise ValidationError('Снимок устарел. Обновите страницу.')
    if multiplier<1:raise ValidationError('Количество единиц в упаковке должно быть больше нуля')
    if snapshot.warehouse=='PP1' and multiplier!=1:raise ValidationError('Для PP1 единицы исходного файла сохраняются: множитель 1')
    source='pp1' if snapshot.warehouse=='PP1' else 'kaspi'
    Item.objects.select_for_update().get(pk=item.pk)
    alias=ItemAlias.objects.filter(source=source,code=snapshot.sku).first()
    if alias:
        if alias.item_id!=item.pk or alias.multiplier!=multiplier:raise ValidationError('Код уже связан иначе. Существующая связь не перезаписана.')
        return alias,False
    alias=ItemAlias.objects.create(source=source,code=snapshot.sku,item=item,multiplier=multiplier,confirmed_by=user)
    return alias,True
