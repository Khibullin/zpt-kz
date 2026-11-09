from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Supplier, PriceUpdate, KaspiReviewTask
from .price_updates import stage_update, advance_update
from .views import access, context


class PriceUpdateForm(forms.Form):
    checked_on = forms.DateField(label='Дата прайсов', initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in [('phaeton', 'Фаэтон'), ('zakazauto', 'ЗаказАвто'), ('omega', 'Омега')]:
            self.fields[key + '_supplier'] = forms.ModelChoiceField(label=label + ' — поставщик',
                queryset=Supplier.objects.order_by('name'), required=False)
            self.fields[key + '_file'] = forms.FileField(label=label + ' — файл XLSX / CSV', required=False,
                widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.csv,.tsv'}))
            self.fields[key + '_full'] = forms.BooleanField(label='Загружаю полный прайс-лист поставщика', required=False,
                help_text='С галочкой — полный прайс: товары, которых нет в файле, будут отмечены как отсутствующие и убраны из «Выгодных к закупке». Без галочки — частичные изменения: обновятся только товары из файла, остальные сохранят прежние цены и наличие.')
            aliases = {'phaeton': ['Phaeton', 'Фаэтон'], 'zakazauto': ['ZakazAuto', 'ЗаказАвто'], 'omega': ['Omega', 'Омега']}
            matches = Supplier.objects.filter(name__in=aliases[key])
            if matches.count() == 1:
                self.fields[key + '_supplier'].initial = matches.first().pk

    def clean(self):
        data = super().clean()
        seen = set()
        count = 0
        for key in ('phaeton', 'zakazauto', 'omega'):
            file, supplier = data.get(key + '_file'), data.get(key + '_supplier')
            if not file:
                continue
            count += 1
            if not supplier:
                self.add_error(key + '_supplier', 'Выберите существующего поставщика.')
            elif supplier.pk in seen:
                self.add_error(key + '_supplier', 'Один поставщик выбран дважды.')
            else:
                seen.add(supplier.pk)
            if file.size > 30 * 1024 * 1024:
                self.add_error(key + '_file', 'Файл превышает 30 МБ.')
            if not file.name.lower().endswith(('.xlsx', '.csv', '.tsv')):
                self.add_error(key + '_file', 'Нужен XLSX или CSV / TSV.')
        if not count:
            raise forms.ValidationError('Выберите хотя бы один файл.')
        return data


@access('sales', 'admin')
def updates(request):
    form = PriceUpdateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            # Validate every supplied file before committing any staged run.
            with transaction.atomic():
                for key in ('phaeton', 'zakazauto', 'omega'):
                    file = form.cleaned_data.get(key + '_file')
                    if file:
                        stage_update(file, form.cleaned_data[key + '_supplier'], request.user,
                                     form.cleaned_data['checked_on'], form.cleaned_data[key + '_full'])
        except ValueError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, 'Файлы проверены. Обновление каталога запущено на этой странице.')
            return redirect('procurement:price_updates')
    batches = PriceUpdate.objects.select_related('supplier').defer('rows')[:30]
    for batch in batches:
        batch.source_conflicts = batch.summary.get('conflict_items', [])
    pending = KaspiReviewTask.objects.filter(status__in=['pending', 'checking', 'blocked']).count()
    return render(request, 'procurement/price_updates.html', context(request,
        form=form, batches=batches, pending=pending,
        review_tasks=KaspiReviewTask.objects.filter(status__in=['pending', 'checking', 'blocked']).select_related('offer__supplier').order_by('priority', 'pk')[:50]))


@access('sales', 'admin')
@require_POST
def advance(request, pk):
    get_object_or_404(PriceUpdate, pk=pk)
    try:
        batch = advance_update(pk)
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
    return JsonResponse({'status': batch.status, 'processed': batch.cursor,
                         'total': batch.row_count, 'summary': batch.summary})


@access('sales', 'admin')
def queue_export(request):
    import json
    from urllib.parse import urlencode
    tasks = KaspiReviewTask.objects.filter(status__in=['pending', 'checking', 'blocked']).select_related('offer__supplier').order_by('priority', 'pk')
    payload = [{'offer_id': t.offer_id, 'supplier': t.offer.supplier.name, 'brand': t.offer.brand,
        'article': t.offer.article, 'name': t.offer.name, 'kaspi_url': t.offer.kaspi_url,
        'reason': t.reason, 'search_url': 'https://kaspi.kz/shop/search/?' + urlencode({
            'text': t.offer.brand + ' ' + t.offer.article, 'c': '750000000'})} for t in tasks]
    response = HttpResponse(json.dumps({'tasks': payload}, ensure_ascii=False), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="kaspi-review-queue.json"'
    return response


@access('sales', 'admin')
@require_POST
def retry_kaspi(request, pk):
    batch = get_object_or_404(PriceUpdate, pk=pk)
    count = KaspiReviewTask.objects.filter(batch=batch, status='blocked').update(status='pending', reason='Повторная проверка по команде сотрудника')
    messages.success(request, f'Возобновлено проверок Kaspi: {count}.')
    return redirect('procurement:price_updates')
