"""Read-only reconciliation for the three optional sources. No stock postings."""
from collections import defaultdict
from django.db.models import Sum
from .models import ImportBatch, StockSnapshot, KaspiOperation, Item, ItemAlias

KINDS = ('pp2', 'stock', 'sales')
LABELS = {'pp2':'Rapida', 'stock':'Kaspi', 'sales':'Продажи'}


def _snapshots(kind):
    batch = ImportBatch.objects.filter(kind=kind).defer('original_data').order_by('-as_of', '-pk').first()
    return batch, list(StockSnapshot.objects.filter(batch=batch).only('sku','name','quantity','brand')) if batch else []


def analyze(chosen):
    chosen = [k for k in KINDS if k in chosen]
    sources={}
    for kind in chosen:
        if kind=='sales':
            batch=ImportBatch.objects.filter(kind='sales').defer('original_data').order_by('-as_of','-pk').first()
            # Sales may span several uploaded reports; fingerprints de-duplicate overlapping lines.
            operations=KaspiOperation.objects.all()
            sales=operations.aggregate(first=__import__('django.db.models',fromlist=['Min']).Min('occurred_on'),
                                         last=__import__('django.db.models',fromlist=['Max']).Max('occurred_on'))
            sources[kind]={'batch':batch,'count':operations.count(),'orders':operations.values('order_id').distinct().count(),
                           'period':sales,'products':operations.exclude(product='').values('product').distinct().count()}
        else:
            batch,rows=_snapshots(kind)
            known=[s.quantity for s in rows if s.quantity is not None]
            codes=[s.sku.strip() for s in rows if s.sku]
            sources[kind]={'batch':batch,'rows':rows,'count':len(rows),'assortment':len(set(codes)),
                           'total':sum(known) if len(known)==len(rows) else None,
                           'unknown':len(rows)-len(known),'duplicates':len(codes)-len(set(codes)),
                           'zeros':sum(q==0 for q in known)}
    result={'chosen':chosen, 'sources':sources, 'ready':bool(chosen),
            'comparisons':[], 'matched':0, 'quantity_mismatch':0, 'different_names':0,
            'unmatched':0, 'sum_difference':None,
            'warning':'', 'total_rapida':None,'total_kaspi':None}
    missing=[LABELS[k] for k,s in sources.items() if not s.get('batch')]
    if missing:
        result['warning']='Не загружены файлы: '+', '.join(missing)
    if 'pp2' not in chosen or 'stock' not in chosen:
        if 'sales' in chosen:
            result['sales_note']='Отчёт финансовых операций не содержит достоверного количества проданных штук. Списаний не выполняем.'
        return result
    rapida=sources['pp2']; kaspi=sources['stock']
    if not rapida['batch'] or not kaspi['batch']:
        return result
    result['total_rapida']=rapida['total']
    result['total_kaspi']=kaspi['total']
    if rapida['total'] is not None and kaspi['total'] is not None:
        result['sum_difference']=rapida['total']-kaspi['total']
    if rapida['batch'].as_of != kaspi['batch'].as_of:
        result['warning']+=' Даты остатков различаются: Rapida '+rapida['batch'].as_of.strftime('%d.%m.%Y')+'; Kaspi '+kaspi['batch'].as_of.strftime('%d.%m.%Y')+'.'

    # Confirmed source aliases take precedence. Exact unique article or code is provisional.
    aliases={(a.source,a.code.strip()):(a.item_id,a.multiplier) for a in ItemAlias.objects.all()}
    articles=defaultdict(set)
    for item_id,code,article in Item.objects.values_list('id','code','article'):
        if article:articles[article.strip()].add(item_id)
        if code:articles[code.strip()].add(item_id)
    def index(rows,source):
        result=defaultdict(list)
        for r in rows:
            code=(r.sku or '').strip()
            alias=aliases.get((source,code))
            if alias:
                key=('item',alias[0]);factor=alias[1];method='Подтверждённый код'
            elif len(articles.get(code,set()))==1:
                key=('item',next(iter(articles[code])));factor=1;method='Точный артикул (предварительно)'
            else:
                key=('code',code);factor=1;method='Код из файла'
            result[key].append({'code':code,'name':r.name or '', 'qty':r.quantity*factor if r.quantity is not None else None,'method':method})
        return result
    left=index(rapida['rows'],'rapida')
    right=index(kaspi['rows'],'kaspi')
    for key in sorted(set(left)|set(right),key=str):
        a=left.get(key,[]);b=right.get(key,[])
        la=a[0] if len(a)==1 else None;lb=b[0] if len(b)==1 else None
        if la is None or lb is None:
            reason=('Несколько строк по коду' if len(a)>1 or len(b)>1 else
                    'Нет на Rapida' if not a else 'Нет на Kaspi')
            result['unmatched']+=1
        elif la['qty'] is None or lb['qty'] is None:
            reason='Нет количества';result['unmatched']+=1
        elif la['qty']!=lb['qty']:
            reason='Разное количество';result['quantity_mismatch']+=1
        elif la['name'].strip().casefold()!=lb['name'].strip().casefold():
            reason='Разные названия (количество совпадает)';result['different_names']+=1
        else:
            reason='Совпадает';result['matched']+=1
        if len(a)==1 and len(b)==1 and (la['method']!='Подтверждённый код' or lb['method']!='Подтверждённый код'):
            reason+=' · связь предварительная'
        result['comparisons'].append({
            'code':(la or lb or a[0] if a else b[0])['code'],
            'rapida_name':la['name'] if la else (a[0]['name'] if a else ''),
            'kaspi_name':lb['name'] if lb else (b[0]['name'] if b else ''),
            'rapida':la['qty'] if la else None,'kaspi':lb['qty'] if lb else None,
            'difference':la['qty']-lb['qty'] if la and lb and la['qty'] is not None and lb['qty'] is not None else None,
            'reason':reason,'ok':reason=='Совпадает'
        })
    result['comparisons'].sort(key=lambda r:(r['ok'],r['code']))
    if 'sales' in chosen:
        result['sales_note']='Продажи показаны справочно. В финансовом отчёте нет подтверждённого количества штук; из текущих остатков ничего повторно не вычитается.'
    return result
