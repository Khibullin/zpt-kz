from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from procurement.views import access
from .forms import ItemForm, AliasForm, DocumentForm, AccountForm, CashForm
from .models import Item, ItemAlias, StockDocument, StockEntry, MoneyAccount, CashEntry
from .services import post_document, receive_transfer

FORMS={'item':(ItemForm,'Новый товар'),'alias':(AliasForm,'Связать код товара'),'document':(DocumentForm,'Новый складской документ'),'account':(AccountForm,'Новый денежный счёт'),'cash':(CashForm,'Денежная операция')}

@never_cache
@access('admin')
def create(request, kind):
    if kind not in FORMS: raise Http404
    cls,title=FORMS[kind]
    form=cls(request.POST or None,initial={'date':date.today(),'opening_date':date.today()})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj=form.save(commit=False)
            if kind == 'alias':obj.confirmed_by=request.user
            elif kind != 'item':obj.created_by=request.user
            obj.save()
        messages.success(request,'Сохранено. Складской документ необходимо провести отдельным действием.' if kind == 'document' else 'Сохранено.')
        return redirect('inventory:documents' if kind == 'document' else 'inventory:cash' if kind in ['cash','account'] else 'inventory:items')
    return render(request,'inventory/form.html',{'form':form,'title':title})

@never_cache
@access('admin')
def items(request):
    q=request.GET.get('q','').strip()
    rows=Item.objects.prefetch_related('aliases').order_by('code')
    if q: rows=rows.filter(Q(code__icontains=q)|Q(article__icontains=q)|Q(name__icontains=q)|Q(aliases__code__icontains=q)).distinct()
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    balances={}
    ids=[i.pk for i in page]
    for r in StockEntry.objects.filter(document__item_id__in=ids).values('document__item_id','warehouse').annotate(total=Sum('quantity')):
        balances.setdefault(r['document__item_id'],{})[r['warehouse']]=r['total']
    for item in page:
        b=balances.get(item.pk,{})
        item.pp1=b.get('PP1');item.pp2=b.get('PP2');item.transit=b.get('TRANSIT')
    return render(request,'inventory/items.html',{'page':page,'q':q})

@never_cache
@access('admin')
def documents(request):
    rows=StockDocument.objects.select_related('item','created_by').prefetch_related('events__user').order_by('-date','-pk')
    for key in ['kind','status','warehouse']:
        if request.GET.get(key):rows=rows.filter(**{key:request.GET[key]})
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(item__code__icontains=q)|Q(item__name__icontains=q)|Q(counterparty__icontains=q)|Q(external_number__icontains=q))
    return render(request,'inventory/documents.html',{'page':Paginator(rows,50).get_page(request.GET.get('page')),'q':q,'kinds':StockDocument.Kind.choices,'statuses':StockDocument._meta.get_field('status').choices})

@never_cache
@access('admin')
@require_POST
def document_action(request,pk):
    try:
        if request.POST.get('action') == 'post':post_document(pk,request.user)
        elif request.POST.get('action') == 'receive':
            raw=request.POST.get('quantity','')
            if not raw.isdigit() or len(raw)>9:raise ValidationError('Укажите целое положительное количество')
            receive_transfer(pk,int(raw),request.user)
        else:raise ValidationError('Неизвестное действие')
        messages.success(request,'Движение товара сохранено.')
    except StockDocument.DoesNotExist:raise Http404
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return redirect('inventory:documents')

@never_cache
@access('admin')
def cash(request):
    rows=CashEntry.objects.select_related('account','stock_document').order_by('-date','-pk')
    q=request.GET.get('q','').strip()
    if q:rows=rows.filter(Q(reference__icontains=q)|Q(counterparty__icontains=q)|Q(category__icontains=q))
    account=request.GET.get('account','')
    if account.isdigit():rows=rows.filter(account_id=account)
    if request.GET.get('kind'):rows=rows.filter(kind=request.GET['kind'])
    accounts=list(MoneyAccount.objects.all().order_by('name'))
    for a in accounts:
        a.current_balance=a.opening_balance
    byid={a.pk:a for a in accounts}
    for row in CashEntry.objects.all():byid[row.account_id].current_balance+=row.signed_amount
    return render(request,'inventory/cash.html',{'page':Paginator(rows,50).get_page(request.GET.get('page')),'accounts':accounts,'kinds':CashEntry._meta.get_field('kind').choices,'q':q})
