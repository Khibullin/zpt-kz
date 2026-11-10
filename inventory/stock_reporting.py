"""Read-only combined balances; snapshots never create ledger entries."""
from collections import defaultdict
from django.db.models import Sum, Max
from .models import ImportBatch, StockSnapshot, StockEntry, Item, ItemAlias
from .matching import candidate_articles
from .labels import supplier_name


def stock_rows(items=None, supplier=''):
    items = list(items if items is not None else Item.objects.prefetch_related('aliases','supplier_links__supplier').order_by('code'))
    batches = {kind: ImportBatch.objects.filter(kind=kind).order_by('-as_of', '-pk').first() for kind in ('pp1', 'stock', 'pp2')}
    snapshots = defaultdict(list)
    for s in StockSnapshot.objects.filter(batch_id__in=[b.pk for b in batches.values() if b]):
        snapshots[s.batch.kind].append(s)
    owners = {(a.source, a.code): a.item_id for a in ItemAlias.objects.all()}
    articles = list(Item.objects.exclude(article='').values_list('article', flat=True))
    candidates = defaultdict(list)
    for s in snapshots['stock']:
        matches = candidate_articles(s.sku, s.name, articles)
        if len(matches) == 1:
            candidates[matches[0]].append(s)
    article_counts = defaultdict(int)
    for article in articles:
        article_counts[article] += 1
    totals=list(StockEntry.objects.values('document__item_id', 'warehouse').annotate(total=Sum('quantity'),last_date=Max('effective_on')))
    ledger = {(r['document__item_id'], r['warehouse']): r['total'] for r in totals}
    dates = {(r['document__item_id'], r['warehouse']): r['last_date'] for r in totals}
    from .models import StockDocument
    origins=list(StockDocument.objects.filter(kind__in=['receipt','opening','adjustment'],status='posted').select_related('item','purchase_order__supplier'))
    origin_names={d.pk:supplier_name(d.purchase_order.supplier.name if d.purchase_order_id else d.counterparty if d.kind=='receipt' else d.item.source_supplier) for d in origins}
    suppliers=defaultdict(set)
    for doc in origins:
        if origin_names[doc.pk]:suppliers[doc.item_id].add(origin_names[doc.pk])
    supplier=supplier_name(supplier)
    for item in items:
        aliases = list(item.aliases.all())
        for attr, kind, source, warehouse in [('pp1', 'pp1', 'pp1', 'PP1'), ('kaspi', 'stock', 'kaspi', None)]:
            links = {a.code: a for a in aliases if a.source == source}
            matched = [s for s in snapshots[kind] if s.sku in links]
            provisional = False
            # Multiple listings can overlap: do not silently sum them.
            if not matched and article_counts[item.article] == 1:
                matched = ([s for s in snapshots[kind] if s.sku == item.article] if kind == 'pp1' else candidates[item.article])
                provisional = bool(matched) and kind == 'stock'
            matched = [s for s in matched if owners.get((source, s.sku), item.pk) == item.pk]
            value = None
            label = 'Нет строки товара в загруженном файле'
            if len(matched) == 1:
                s = matched[0]
                value = s.quantity * (links[s.sku].multiplier if s.sku in links else 1) if s.quantity is not None else None
                label = ('В файле не указано количество · ' if s.quantity is None else '') + f'Файл от {s.batch.as_of:%d.%m.%Y}' + (' · сопоставление предварительное' if provisional else '')
            elif len(matched) > 1:
                label = 'Несколько SKU · требуется сверка'
            setattr(item, attr + '_present', bool(matched))
            setattr(item, attr + '_snapshot_present', bool(matched))
            setattr(item, attr + '_snapshot', value)
            setattr(item, attr + '_snapshot_source', label)
            if warehouse and (item.pk, warehouse) in ledger:
                value = ledger[item.pk, warehouse]
                label = 'По складскому учёту' + (f' · по {dates[item.pk,warehouse]:%d.%m.%Y}' if dates.get((item.pk,warehouse)) else '')
            setattr(item, attr, value)
            setattr(item, attr + '_source', label)
        item.pp2_snapshot = item.kaspi_snapshot
        item.pp2_snapshot_source = 'По Kaspi · ' + item.kaspi_snapshot_source
        # A separate Rapida export supersedes the historical Kaspi fallback.
        if batches.get('pp2'):
            pp2_links={a.code:a for a in aliases if a.source=='rapida'}
            matched=[s for s in snapshots['pp2'] if s.sku==item.article or s.sku in pp2_links]
            matched=[s for s in matched if owners.get(('rapida',s.sku),item.pk)==item.pk]
            item.pp2_snapshot=None
            item.pp2_snapshot_source='Нет строки товара в файле PP2'
            if len(matched)==1:
                snap=matched[0]
                item.pp2_snapshot=snap.quantity* (pp2_links[snap.sku].multiplier if snap.sku in pp2_links else 1) if snap.quantity is not None else None
                item.pp2_snapshot_source=f'Файл PP2 от {snap.batch.as_of:%d.%m.%Y}'
                if snap.quantity is None:item.pp2_snapshot_source+=' · количество не указано'
            elif len(matched)>1:item.pp2_snapshot_source='Несколько строк PP2 · требуется сопоставление кодов'
            item.pp2_present=bool(matched)
            item.pp2_snapshot_present=bool(matched)
        else:
            item.pp2_present=item.kaspi_present
            item.pp2_snapshot_present=item.kaspi_snapshot_present
        item.pp2 = ledger.get((item.pk, 'PP2'), item.pp2_snapshot)
        item.pp2_source = ('По складскому учёту' + (f' · по {dates[item.pk,"PP2"]:%d.%m.%Y}' if dates.get((item.pk,'PP2')) else '')) if (item.pk, 'PP2') in ledger else item.pp2_snapshot_source
        item.pp1_posted = (item.pk, 'PP1') in ledger
        item.pp2_posted = (item.pk, 'PP2') in ledger
        item.pp1_present = item.pp1_present or item.pp1_posted
        item.pp2_present = item.pp2_present or item.pp2_posted
        item.total_posted = item.pp1_posted or item.pp2_posted
        item.supplier_names=sorted(suppliers[item.pk] | ({supplier_name(item.source_supplier)} if item.source_supplier else set()))
        item.supplier_label='; '.join(item.supplier_names) or 'Не указан'
        item.matches_supplier=not supplier or supplier in item.supplier_names
        from .costing import available_lots
        from django.core.exceptions import ValidationError
        item.valuation_error=False
        origin_history=defaultdict(set)
        try:lots=available_lots(item,include_transit=True,origins_by_warehouse=origin_history) if item.total_posted else {}
        except ValidationError:
            lots={};item.valuation_error=True
        item.current_lots={wh:[lot for lot in group if not supplier or origin_names.get(lot['origin'])==supplier] for wh,group in lots.items()}
        if supplier and item.matches_supplier:
            for attr,warehouse in [('pp1','PP1'),('pp2','PP2')]:
                if getattr(item,attr+'_posted'):
                    setattr(item,attr,sum(lot['quantity'] for lot in lots.get(warehouse,[]) if origin_names.get(lot['origin'])==supplier))
                elif supplier_name(item.source_supplier)!=supplier:
                    setattr(item,attr,None)
                if supplier_name(item.source_supplier)!=supplier:
                    setattr(item,attr+'_snapshot',None)
                    setattr(item,attr+'_snapshot_present',False)
                setattr(item,attr+'_present',any(origin_names.get(origin)==supplier for origin in origin_history[warehouse]) or (supplier_name(item.source_supplier)==supplier and getattr(item,attr+'_present')))
            if supplier_name(item.source_supplier)!=supplier:item.kaspi=None
            item.supplier_label=supplier
        for attr in ['pp1','pp2']:
            if not getattr(item,attr+'_present') and item.total_posted and (supplier or not item.source_supplier):
                setattr(item,attr,0)
                setattr(item,attr+'_source','Движений по этому складу нет')
        known=[v for v in [item.pp1,item.pp2] if v is not None]
        item.total=sum(known) if len(known)==2 else None
        item.partial_total=len(known)==1
        item.total_present=item.pp1_present or item.pp2_present
        item.difference = item.pp2 - item.kaspi if item.pp2 is not None and item.kaspi is not None else None
        item.transit = ledger.get((item.pk, 'TRANSIT'))
        item.damaged = ledger.get((item.pk, 'DAMAGED'))
        if supplier and item.matches_supplier and item.total_posted:
            for attr,warehouse in [('transit','TRANSIT'),('damaged','DAMAGED')]:
                if getattr(item,attr) is not None:setattr(item,attr,sum(lot['quantity'] for lot in lots.get(warehouse,[]) if origin_names.get(lot['origin'])==supplier))
    return items


def stock_summary(rows):
    result = []
    for field, label in [('pp1', 'PP1'), ('pp2', 'PP2 · Rapida'), ('kaspi', 'Kaspi'), ('total', 'Всего PP1 + PP2')]:
        groups = []
        for unit, unit_label in Item._meta.get_field('unit').choices:
            values = [getattr(i, field) for i in rows if i.unit == unit and getattr(i, field) is not None]
            if values:
                groups.append({'unit': unit_label, 'quantity': sum(values)})
        result.append({'label': label, 'groups': groups, 'unknown': sum(getattr(i, field) is None for i in rows)})
    return result


def stock_export(rows):
    fields = ['pp1', 'pp2', 'kaspi', 'total', 'difference', 'transit', 'damaged']
    data = [[i.code, i.article, i.name, i.brand, i.get_unit_display(), *[getattr(i, f) if getattr(i, f) is not None else 'Не указано' for f in fields], i.pp1_source, i.pp2_source, i.kaspi_source, i.supplier_label] for i in rows]
    data.append(['Всего наименований', len(rows)])
    for column in stock_summary(rows):
        for group in column['groups']:
            data.append([column['label'], group['unit'], 'Сумма известных остатков', group['quantity']])
        data.append([column['label'], 'Без установленного остатка', column['unknown']])
    return data

STOCK_HEADERS = ['Код', 'Артикул', 'Название', 'Бренд', 'Единица', 'PP1', 'PP2', 'Kaspi', 'Всего PP1 + PP2', 'PP2 − Kaspi', 'В пути', 'Брак', 'Источник PP1', 'Источник PP2', 'Источник Kaspi', 'Поставщик']
