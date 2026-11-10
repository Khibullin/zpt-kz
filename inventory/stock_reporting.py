"""Read-only combined balances; snapshots never create ledger entries."""
from collections import defaultdict
from django.db.models import Sum
from .models import ImportBatch, StockSnapshot, StockEntry, Item, ItemAlias
from .matching import candidate_articles


def stock_rows(items=None):
    items = list(items if items is not None else Item.objects.prefetch_related('aliases','supplier_links__supplier').order_by('code'))
    batches = {kind: ImportBatch.objects.filter(kind=kind).order_by('-as_of', '-pk').first() for kind in ('pp1', 'stock')}
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
    ledger = {(r['document__item_id'], r['warehouse']): r['total'] for r in StockEntry.objects.values('document__item_id', 'warehouse').annotate(total=Sum('quantity'))}
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
            label = 'Не указано'
            if len(matched) == 1:
                s = matched[0]
                value = s.quantity * (links[s.sku].multiplier if s.sku in links else 1) if s.quantity is not None else None
                label = f'Файл от {s.batch.as_of:%d.%m.%Y}' + (' · сопоставление предварительное' if provisional else '')
            elif len(matched) > 1:
                label = 'Несколько SKU · требуется сверка'
            if warehouse and (item.pk, warehouse) in ledger:
                value = ledger[item.pk, warehouse]
                label = 'Проведённые документы'
            setattr(item, attr, value)
            setattr(item, attr + '_source', label)
        item.pp2 = ledger.get((item.pk, 'PP2'), item.kaspi)
        item.pp2_source = 'Проведённые документы' if (item.pk, 'PP2') in ledger else 'По Kaspi · ' + item.kaspi_source
        item.pp1_posted = (item.pk, 'PP1') in ledger
        item.pp2_posted = (item.pk, 'PP2') in ledger
        item.total = item.pp1 + item.pp2 if item.pp1 is not None and item.pp2 is not None else None
        item.difference = item.pp2 - item.kaspi if item.pp2 is not None and item.kaspi is not None else None
        item.transit = ledger.get((item.pk, 'TRANSIT'))
        item.damaged = ledger.get((item.pk, 'DAMAGED'))
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
    data = [[i.code, i.article, i.name, i.brand, i.get_unit_display(), *[getattr(i, f) if getattr(i, f) is not None else 'Не указано' for f in fields], i.pp1_source, i.pp2_source, i.kaspi_source, "; ".join(link.supplier.name for link in i.supplier_links.all())] for i in rows]
    data.append(['Всего наименований', len(rows)])
    for column in stock_summary(rows):
        for group in column['groups']:
            data.append([column['label'], group['unit'], 'Сумма известных остатков', group['quantity']])
        data.append([column['label'], 'Без установленного остатка', column['unknown']])
    return data

STOCK_HEADERS = ['Код', 'Артикул', 'Название', 'Бренд', 'Единица', 'PP1', 'PP2', 'Kaspi', 'Всего PP1 + PP2', 'PP2 − Kaspi', 'В пути', 'Брак', 'Источник PP1', 'Источник PP2', 'Источник Kaspi', 'Закуплено у поставщика']
