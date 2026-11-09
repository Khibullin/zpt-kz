import io
import json
import requests
from datetime import date
from django import forms
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from openpyxl import Workbook
from .views import access,context
from .models import RetailListing,Supplier
from .retail import prepare_updates,apply_updates,publish_one,validate_publication


class RetailUploadForm(forms.Form):
    file=forms.FileField(label='Excel, CSV или пакет карточек JSON')
    checked_on=forms.DateField(label='Дата актуальности',initial=timezone.localdate,widget=forms.DateInput(attrs={'type':'date'}))
    def clean_file(self):
        f=self.cleaned_data['file']
        if f.size>20*1024*1024 or not f.name.lower().endswith(('.xlsx','.csv','.json')):raise forms.ValidationError('XLSX, CSV или JSON, до 20 МБ.')
        return f


@access('admin','sales')
def retail_workspace(request):
    form=RetailUploadForm(request.POST or None,request.FILES or None)
    pending=None
    if request.method=='POST':
        try:
            if request.POST.get('action') in ('publish_stock', 'publish_missing_stock'):
                from .stock_retail import start_publication
                run = start_publication(request.user, missing_only=request.POST.get('action') == 'publish_missing_stock')
                messages.success(request, f'Публикация наличия запущена: {run.total} позиций к проверке. Цена — закупка +30%, без Kaspi.')
                return redirect('procurement:retail')
            if request.POST.get('action') == 'publish_best':
                from .retail_bulk import publication_candidates, publish_candidate
                from collections import Counter
                candidates = publication_candidates(request.user)
                # Bounded writes keep the existing web service responsive.
                remaining = [row for row in candidates if not RetailListing.objects.filter(
                    offer=row[0], source_content__pricing_policy='kaspi_minus_5', product__price=row[1]).exists()]
                counts = Counter(publish_candidate(row[0].pk, request.user) for row in remaining[:20])
                messages.success(request, f'Опубликовано/обновлено: {counts["published"]}; дубли: {counts["duplicate"]}; пропущено: {counts["skipped"]}. Осталось проверить: {max(0, len(remaining)-20)}.')
                return redirect('procurement:retail')
            if request.POST.get('action')=='apply':
                payload=request.session.pop('omega_retail_preview',None)
                if not payload:raise ValueError('Загрузите файл для предпросмотра.')
                if payload['kind']=='cards':
                    # One card per request avoids remote image-download timeouts.
                    if len(payload['rows'])!=1:raise ValueError('Публикуйте по одной карточке за раз.')
                    listing=publish_one(payload['rows'][0],request.user)
                    messages.success(request,'Карточка AG Parts сохранена.'+(' Требует решения менеджера.' if listing.hold_reason else ' Покупка на ZPT доступна.'))
                else:
                    count=apply_updates(payload['rows'],request.user)
                    messages.success(request,f'Обновлено товаров: {count}. Цены пересчитаны с наценкой 30% и ограничением Kaspi.')
                return redirect('procurement:retail')
            elif form.is_valid():
                f=form.cleaned_data['file']
                if f.name.lower().endswith('.json'):
                    rows=validate_publication(json.load(f));kind='cards'
                else:
                    rows=prepare_updates(f,form.cleaned_data['checked_on']);kind='updates'
                pending={'kind':kind,'rows':rows}
                request.session['omega_retail_preview']=pending
        except (ValueError,KeyError,TypeError,OverflowError,ObjectDoesNotExist,ValidationError,requests.RequestException) as error:
            messages.error(request,str(error))
    from .models import RetailPublicationRun
    from django.db.models import Count
    listings = RetailListing.objects.select_related('offer','offer__supplier','product').order_by('pk')
    run = RetailPublicationRun.objects.order_by('-pk').first()
    published_summary = listings.filter(product__status='active', source_content__pricing_policy='supplier_plus_30').values('offer__supplier__name').annotate(count=Count('pk')).order_by('offer__supplier__name')
    return render(request,'procurement/retail.html',context(request,
        supplier=Supplier.objects.filter(name='Omega').first(),form=form,pending=pending,
        listings=listings[:100],listing_total=listings.count(),publication_run=run,published_summary=published_summary))


@access('admin','sales')
def retail_excel(request):
    book=Workbook();sheet=book.active;sheet.title='Корректировка AG Parts'
    sheet.append(['brand','article','cost','stock','active','min_order'])
    for item in RetailListing.objects.select_related('offer').filter(offer__supplier__name='Omega').order_by('pk'):
        sheet.append([item.offer.brand,item.offer.article,float(item.offer.cost),item.offer.stock,int(item.enabled),item.offer.min_order])
    sheet.freeze_panes='A2'
    for col,width in [('A',22),('B',24),('C',18),('D',22),('E',18)]:sheet.column_dimensions[col].width=width
    output=io.BytesIO();book.save(output)
    response=HttpResponse(output.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']='attachment; filename="omega-agparts-update.xlsx"'
    return response
