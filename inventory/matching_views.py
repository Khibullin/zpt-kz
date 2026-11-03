from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from procurement.views import access
from .models import Item, StockSnapshot
from .matching import reconciliation_rows, confirm_mapping, prepare_items

class MappingForm(forms.Form):
    snapshot=forms.ModelChoiceField(queryset=StockSnapshot.objects.all(),widget=forms.HiddenInput)
    item=forms.ModelChoiceField(label='Наш товар',queryset=Item.objects.order_by('code'))
    unit=forms.ChoiceField(label='Единица учёта товара',choices=[('', 'Выберите'),('piece','Штука'),('set','Комплект')])
    multiplier=forms.IntegerField(label='Единиц в упаковке',min_value=1,initial=1)
    checked=forms.BooleanField(label='Проверены артикул, тип товара и комплектность')

@never_cache
@access('admin')
def reconciliation(request):
    rows=reconciliation_rows()
    q=request.GET.get('q','').strip().casefold()
    state=request.GET.get('state','')
    warehouse=request.GET.get('warehouse','')
    total=len(rows)
    confirmed=sum(bool(row['alias']) for row in rows)
    if q:rows=[r for r in rows if q in (r['snapshot'].sku+' '+r['snapshot'].name+' '+' '.join(r['candidates'])).casefold()]
    if state=='mapped':rows=[r for r in rows if r['alias']]
    if state=='unmapped':rows=[r for r in rows if not r['alias']]
    if state=='multiple':rows=[r for r in rows if r.get('shared_count',0)>1]
    if warehouse:rows=[r for r in rows if r['snapshot'].warehouse==warehouse]
    page=Paginator(rows,30).get_page(request.GET.get('page'))
    all_items=list(Item.objects.order_by('code'))
    item_choices=[('', '---------')]+[(item.pk,str(item)) for item in all_items]
    byarticle={}
    for item in all_items:
        byarticle.setdefault(item.article,[]).append(item)
    for row in page:
        matches=byarticle.get(row['candidates'][0],[]) if len(row['candidates'])==1 else []
        suggested=matches[0] if len(matches)==1 else None
        row['form']=MappingForm(initial={'snapshot':row['snapshot'],'item':suggested,'unit':suggested.unit if suggested and suggested.unit!='pending' else ''},auto_id=f"snapshot_{row['snapshot'].pk}_%s")
        row['form'].fields['item'].choices=item_choices
    query=request.GET.copy();query.pop('page',None)
    return render(request,'inventory/reconciliation.html',{'page':page,'q':request.GET.get('q',''),'total':total,'confirmed':confirmed,'query':query.urlencode()})

@never_cache
@access('admin')
@require_POST
def confirm(request):
    form=MappingForm(request.POST)
    if form.is_valid():
        try:
            alias,created=confirm_mapping(form.cleaned_data['snapshot'].pk,form.cleaned_data['item'],form.cleaned_data['multiplier'],request.user,unit=form.cleaned_data['unit'])
            messages.success(request,'Связь сохранена. Остаток не изменён.' if created else 'Эта связь уже сохранена.')
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    else:messages.error(request,'Выберите товар, количество единиц в упаковке и подтвердите проверку комплектности.')
    return redirect('inventory:reconciliation')


@never_cache
@access('admin')
@require_POST
def prepare(request):
    try:
        created,existing=prepare_items(request.user)
        messages.success(request,f'Подготовлено карточек: {created}. Уже существовало: {existing}. Единицы учёта требуют проверки; остатки не изменены.')
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return redirect('inventory:reconciliation')
