"""Read-only same-moment comparison of Rapida PP2 and Kaspi inventory snapshots."""
from collections import defaultdict
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Sum, Count, Q
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from .permissions import accounting_access
from .models import ImportBatch, StockSnapshot, KaspiOperation, Item, ItemAlias
from .update_views import UpdateForm, SOURCES


def _resolve(snapshot, source, alias_map, article_map):
    code = snapshot.sku.strip()
    match = alias_map.get((source, code))
    if match:
        return match[0], match[1], 'Подтверждённый код'
    candidates = article_map.get(code, [])
    if len(candidates) == 1:
        return candidates[0], 1, 'Точный уникальный артикул · проверить'
    return None, 1, 'Не сопоставлен'


@never_cache
@accounting_access
def comparison(request):
    batches = {kind: ImportBatch.objects.filter(kind=kind).defer('original_data').order_by('-as_of', '-pk').first()
               for kind in ('pp2', 'stock', 'sales', 'pp1')}
    counts = {}
    for kind, batch in batches.items():
        counts[kind] = (KaspiOperation.objects.filter(batch=batch).count() if kind == 'sales' else
                        StockSnapshot.objects.filter(batch=batch).count()) if batch else 0
    aliases = {(a.source, a.code.strip()): (a.item_id, a.multiplier)
               for a in ItemAlias.objects.all()}
    article_map = defaultdict(list)
    names = {}
    for i in Item.objects.only('id', 'article', 'name', 'brand', 'code'):
        if i.article.strip():
            article_map[i.article.strip()].append(i.pk)
        names[i.pk] = (i.article or i.code, i.name, i.brand)
    source_data = {}
    for kind, source in [('pp2', 'rapida'), ('stock', 'kaspi')]:
        mapped = defaultdict(list)
        unmatched = []
        batch = batches[kind]
        if batch:
            for snap in StockSnapshot.objects.filter(batch=batch, warehouse='PP2').only('sku', 'name', 'brand', 'quantity', 'price'):
                item_id, multiplier, method = _resolve(snap, source, aliases, article_map)
                entry = {'code': snap.sku, 'name': snap.name, 'quantity': snap.quantity,
                         'units': snap.quantity * multiplier if snap.quantity is not None else None,
                         'method': method}
                if item_id:
                    mapped[item_id].append(entry)
                else:
                    unmatched.append(entry)
        source_data[kind] = {'mapped': mapped, 'unmatched': unmatched}
    items = []
    matches = differences = incomplete = 0
    extra = missing = 0
    known_pairs = 0
    if batches['pp2'] and batches['stock']:
        for pk in sorted(set(source_data['pp2']['mapped']) | set(source_data['stock']['mapped'])):
            left = source_data['pp2']['mapped'].get(pk, [])
            right = source_data['stock']['mapped'].get(pk, [])
            a = left[0]['units'] if len(left) == 1 else None
            b = right[0]['units'] if len(right) == 1 else None
            comparable = bool(len(left) == len(right) == 1 and a is not None and b is not None)
            delta = a - b if comparable else None
            if comparable:
                known_pairs += 1
                if delta == 0:
                    matches += 1
                else:
                    differences += 1
                    extra += max(delta, 0)
                    missing += max(-delta, 0)
            else:
                incomplete += 1
            code, name, brand = names.get(pk, ('', '', ''))
            items.append({'code': code, 'name': name, 'brand': brand, 'rapida': a, 'kaspi': b,
                          'difference': delta, 'state': 'Совпадает' if delta == 0 and comparable else
                          'Излишек Rapida' if comparable and delta > 0 else
                          'Kaspi больше' if comparable else 'Требует проверки',
                          'provisional': any(r['method'] != 'Подтверждённый код' for r in left + right),
                          'ambiguous': len(left) > 1 or len(right) > 1})
    query = request.GET.get('q', '').strip().casefold()
    status = request.GET.get('status', 'different')
    if query:
        items = [i for i in items if query in (' '.join([i['code'], i['name'], i['brand']])).casefold()]
    if status == 'different':
        items = [i for i in items if i['difference'] != 0 or i['difference'] is None]
    elif status == 'matched':
        items = [i for i in items if i['difference'] == 0]
    elif status == 'unmapped':
        items = [i for i in items if i['difference'] is None or i['provisional']]
    if request.GET.get('export') == 'xlsx':
        from .tools_views import xlsx_response
        return xlsx_response('pp2-kaspi-check',
                             ['Артикул', 'Название', 'Бренд', 'Rapida PP2', 'Kaspi PP2', 'Разница', 'Статус', 'Связь требует подтверждения'],
                             ([i['code'], i['name'], i['brand'], i['rapida'], i['kaspi'], i['difference'], i['state'], 'Да' if i['provisional'] else 'Нет'] for i in items))
    sales = KaspiOperation.objects.all()
    if batches['sales']:
        # Financial rows are already deduplicated by fingerprint in the importer.
        sales = sales.filter(batch=batches['sales'])
    else:
        sales = sales.none()
    financial = sales.aggregate(revenue=Sum('revenue'), commission=Sum('commission'),
                                delivery=Sum('delivery'), pay_fee=Sum('pay_fee'),
                                product_bonus=Sum('product_bonus'), review_bonus=Sum('review_bonus'),
                                other_fees=Sum('other_fees'))
    financial['net'] = sum((financial[k] or Decimal('0')) for k in financial)
    dates = [b.as_of for b in (batches['pp2'], batches['stock']) if b]
    tab = request.GET.get('tab', 'results')
    if tab not in ('results', 'sales', 'rapida', 'kaspi', 'history'):
        tab = 'results'
    selected = batches['pp2'] if tab == 'rapida' else batches['stock'] if tab == 'kaspi' else None
    source_page = Paginator(StockSnapshot.objects.filter(batch=selected, warehouse='PP2').order_by('sku', 'pk') if selected else
                            StockSnapshot.objects.none(), 50).get_page(request.GET.get('page'))
    history = ImportBatch.objects.filter(kind__in=['pp2', 'stock', 'sales']).defer('original_data').order_by('-created_at')[:30]
    return render(request, 'inventory/comparison.html', {
        'batches': batches, 'counts': counts, 'form': UpdateForm(),
        'has_sources': bool(batches['pp2'] and batches['stock']),
        'same_date': len(dates) == 2 and dates[0] == dates[1],
        'matches': matches, 'differences': differences, 'incomplete': incomplete,
        'extra': extra, 'missing': missing, 'known_pairs': known_pairs,
        'unmapped_rapida': len(source_data['pp2']['unmatched']),
        'unmapped_kaspi': len(source_data['stock']['unmatched']),
        'page': Paginator(items, 50).get_page(request.GET.get('page')),
        'source_page': source_page, 'history': history, 'tab': tab,
        'status': status, 'q': request.GET.get('q', '').strip(),
        'financial': financial, 'sales_count': sales.count(),
        'recent_sales': sales.order_by('-occurred_on', '-pk')[:30],
    })
