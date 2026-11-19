"""Editable first stage of supplier-pricelist preselection.

This view previews the saved criteria against imported offers. It does not modify
supplier imports, stock, public catalogue, Kaspi prices, or existing ROI rules.
"""
from datetime import date
from hashlib import sha256
import json
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.contrib import messages
from django.db.models import Q, Count, Max
from django.shortcuts import redirect, render

from .models import Offer, Supplier, WholesaleSelectionRule
from .views import access, context

GROUPS = (
    ('consumables', 'Расходники'),
    ('suspension', 'Ходовая и подвеска'),
    ('engine', 'Двигатель'),
    ('electrical', 'Электрика'),
    ('brakes', 'Тормозная система'),
    ('cooling', 'Охлаждение'),
    ('body', 'Кузов и оптика'),
    ('other', 'Другие категории'),
)
# A grouping aid for supplier lines without an explicit category. Classification
# here is a preliminary guess, never a verified vehicle fitment or exact part ID.
KEYWORDS = {
    'consumables': ('фильтр', 'масло', 'свеча', 'щетк', 'щётк', 'ремень', 'filter', 'oil'),
    'suspension': ('подвес', 'рычаг', 'сайлентблок', 'шаровая', 'рулев', 'ступиц', 'амортиз', 'стойк'),
    'engine': ('двигател', 'мотор', 'поршен', 'коленвал', 'грм', 'прокладк', 'engine'),
    'electrical': ('электр', 'катушк', 'стартер', 'генератор', 'реле', 'датчик'),
    'brakes': ('тормоз', 'колодк', 'суппорт', 'brake'),
    'cooling': ('охлажден', 'радиатор', 'термостат', 'помпа', 'антифриз'),
    'body': ('кузов', 'фара', 'бампер', 'зеркал', 'фонар'),
}


class SelectionRuleForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.filter(name='Omega'),
                                     required=True, empty_label=None)
    min_cost = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2,
                                  widget=forms.NumberInput(attrs={'min': '0', 'step': '100'}))
    max_vehicle_age = forms.IntegerField(min_value=0, max_value=100,
                                         widget=forms.NumberInput(attrs={'min': '0', 'max': '100'}))
    category_groups = forms.MultipleChoiceField(choices=GROUPS, required=False,
                                               widget=forms.CheckboxSelectMultiple)
    classification_mode = forms.ChoiceField(choices=(
        ('auto', 'Из прайса или автоматически по наименованию'),
        ('supplier', 'Только категория из прайса'),
    ))
    in_stock_only = forms.BooleanField(required=False, initial=True)


def _group_filter(groups, mode):
    if not groups or 'other' in groups:
        return Q()
    combined = Q(pk__in=[])
    for group in groups:
        for word in KEYWORDS.get(group, ()):
            combined |= Q(category__icontains=word)
            if mode == 'auto':
                combined |= Q(name__icontains=word)
    return combined


def _stock_filter():
    # Unknown or zero stock must not be claimed available. Supplier stock is
    # stored as a string; accept positive quantities and explicit presence words.
    return Q(stock__iregex=r'^\s*[1-9][0-9]*(?:[.,][0-9]+)?(?:\s|$)') | Q(
        stock__iregex=r'(^|\s)(в наличии|есть|available)(\s|$)'
    )


@access('sales', 'admin')
def selection_rules(request):
    omega = Supplier.objects.get(name='Omega')
    rule, _ = WholesaleSelectionRule.objects.get_or_create(singleton_key=1)
    from .price_update_views import SupplierPriceUploadForm, price_history_context
    uploading_price = request.method == 'POST' and request.POST.get('action') == 'upload_price'
    price_form = SupplierPriceUploadForm(request.POST if uploading_price else None,
        request.FILES if uploading_price else None, initial={'supplier': omega.pk}, auto_id='price_%s')
    if uploading_price and price_form.is_valid():
        from .price_updates import stage_update
        try:
            stage_update(price_form.cleaned_data['file'], price_form.cleaned_data['supplier'],
                         request.user, price_form.cleaned_data['checked_on'], price_form.cleaned_data['full_catalog'])
        except ValueError as error:
            price_form.add_error(None, str(error))
        else:
            messages.success(request, 'Прайс принят. Ход обработки сохранён в истории загрузок.')
            return redirect('procurement:selection_rules')
    initial = {
        'supplier': omega.pk,
        'min_cost': rule.min_cost,
        'max_vehicle_age': rule.max_vehicle_age,
        'category_groups': rule.category_groups,
        'in_stock_only': rule.in_stock_only,
        'classification_mode': rule.classification_mode,
    }
    if request.method == 'POST' and request.POST.get('action') == 'import_apify':
        from .apify_buying_import import import_apify_results
        uploaded = request.FILES.get('apify_json')
        if uploaded is None:
            messages.error(request, 'Выберите JSON-файл результатов Apify.')
        else:
            try:
                result = import_apify_results(uploaded, uploaded.name)
            except ValidationError as error:
                messages.error(request, '; '.join(error.messages))
            except (ValueError, TypeError) as error:
                messages.error(request, 'Не удалось прочитать данные Apify: неверный формат.')
            else:
                messages.success(request,
                    f"Загружено: {result['products']} товаров; "
                    f"связано с оптовыми позициями: {result['linked'] + result['verified']}; "
                    f"требуют сопоставления: {result['unmatched']}.")
        return redirect('procurement:selection_rules')
    if request.method == 'POST' and not uploading_price:
        form = SelectionRuleForm(request.POST)
        if request.POST.get('action') == 'reset':
            rule.supplier = omega
            rule.min_cost = Decimal('2000')
            rule.max_vehicle_age = 20
            rule.category_groups = []
            rule.in_stock_only = True
            rule.classification_mode = 'auto'
            rule.updated_by = request.user
            rule.save()
            messages.success(request, 'Установлены исходные значения предварительного отбора.')
            return redirect('procurement:selection_rules')
        if form.is_valid():
            data = form.cleaned_data
            rule.supplier = data['supplier']
            rule.min_cost = data['min_cost']
            rule.max_vehicle_age = data['max_vehicle_age']
            rule.category_groups = data['category_groups']
            rule.in_stock_only = data['in_stock_only']
            rule.classification_mode = data['classification_mode']
            rule.updated_by = request.user
            rule.save()
            messages.success(request, 'Правила предварительного отбора сохранены.')
            return redirect('procurement:selection_rules')
    else:
        form = SelectionRuleForm(initial=initial)

    # Preview against the already imported offers. No destructive updates or
    # automatic purchases / Kaspi calls are initiated by this screen.
    effective = form.cleaned_data if form.is_bound and form.is_valid() else initial
    rows = Offer.objects.filter(supplier=omega)
    if effective.get('supplier'):
        supplier = effective['supplier']
        rows = rows.filter(supplier_id=supplier.pk if hasattr(supplier, 'pk') else supplier)
    rows = rows.filter(cost__gte=effective.get('min_cost') or 0)
    if effective.get('in_stock_only'):
        rows = rows.filter(_stock_filter())
    rows = rows.filter(_group_filter(effective.get('category_groups') or [],
                                   effective.get('classification_mode', 'auto')))
    # A page refresh must not repeatedly scan tens of thousands of fitments.
    # Short TTL bounds staleness after external imports and avoids caching prices.
    age = int(effective.get('max_vehicle_age', 20))
    supplier_key = effective.get('supplier')
    supplier_key = getattr(supplier_key, 'pk', supplier_key)
    criteria = {
        'supplier': supplier_key,
        'min_cost': str(effective.get('min_cost') or 0),
        'stock': bool(effective.get('in_stock_only')),
        'groups': sorted(effective.get('category_groups') or []),
        'mode': effective.get('classification_mode', 'auto'),
        'age': age,
        'year': date.today().year,
    }
    stats_key = 'zpt:selection-preview:v1:' + sha256(
        json.dumps(criteria, sort_keys=True).encode('utf-8')
    ).hexdigest()
    stats = cache.get(stats_key) if request.method == 'GET' else None
    if stats is None:
        before_years = rows.count()
        # No source years: do not infer vehicle fitment.
        years = range(max(1980, date.today().year-age), date.today().year+2)
        year_pattern = '(' + '|'.join(str(year) for year in years) + ')'
        checked_rows = rows.filter(
            Q(applicability__regex=year_pattern) |
            Q(fitments__verified=True, fitments__details__regex=year_pattern)
        ).distinct()
        matched = checked_rows.count()
        stats = (before_years, matched)
        if request.method == 'GET':
            cache.set(stats_key, stats, timeout=60)
    before_years, matched = stats
    unknown_years = max(0, before_years - matched)
    # All download actions use the last saved rule, not unsaved form values.
    # Preview is intentionally short and read-only; full records go to Excel.
    from .selection_exports import filtered_offers, JSON_BATCH_SIZE
    saved_rows = filtered_offers(rule)
    exported_total = saved_rows.count()
    batches = (exported_total + JSON_BATCH_SIZE - 1) // JSON_BATCH_SIZE
    from .models import KaspiBuyingCandidate
    # Compact saved-result summary; the full card list lives only in the buying catalogue.
    buying_qs = KaspiBuyingCandidate.objects.filter(offer__supplier=omega)
    buying_total = buying_qs.filter(
        match_status__in=['candidate', 'verified'], offer__isnull=False,
    ).count()
    last_import = (buying_qs.exclude(source_filename='')
        .values('source_filename')
        .annotate(saved=Count('id'),
                  linked=Count('id', filter=Q(match_status__in=['candidate', 'verified'], offer__isnull=False)),
                  verified=Count('id', filter=Q(match_status='verified', offer__isnull=False)),
                  unmatched=Count('id', filter=Q(match_status='unmatched')),
                  last_saved=Max('imported_at'))
        .order_by('-last_saved', 'source_filename').first())
    return render(request, 'procurement/selection_rules.html',
                  context(request, form=form, rule=rule, matched=exported_total,
                          price_form=price_form, **price_history_context(request),
                          year_unknown=unknown_years, before_years=before_years,
                          group_choices=GROUPS,
                          json_batches=range(1, batches + 1),
                          json_batch_size=JSON_BATCH_SIZE,
                          buying_total=buying_total, last_import=last_import))
