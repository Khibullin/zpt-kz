"""Editable first stage of supplier-pricelist preselection.

This view previews the saved criteria against imported offers. It does not modify
supplier imports, stock, public catalogue, Kaspi prices, or existing ROI rules.
"""
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.db.models import Q
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
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.all().order_by('name'),
                                     required=False, empty_label='Все поставщики')
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
    return Q(stock__iregex=r'^\\s*[1-9][0-9]*(?:[.,][0-9]+)?(?:\\s|$)') | Q(
        stock__iregex=r'(^|\\s)(в наличии|есть|available)(\\s|$)'
    )


@access('sales', 'admin')
def selection_rules(request):
    rule, _ = WholesaleSelectionRule.objects.get_or_create(singleton_key=1)
    initial = {
        'supplier': rule.supplier_id,
        'min_cost': rule.min_cost,
        'max_vehicle_age': rule.max_vehicle_age,
        'category_groups': rule.category_groups,
        'in_stock_only': rule.in_stock_only,
        'classification_mode': rule.classification_mode,
    }
    if request.method == 'POST':
        form = SelectionRuleForm(request.POST)
        if request.POST.get('action') == 'reset':
            rule.supplier = None
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
    rows = Offer.objects.all()
    if effective.get('supplier'):
        supplier = effective['supplier']
        rows = rows.filter(supplier_id=supplier.pk if hasattr(supplier, 'pk') else supplier)
    rows = rows.filter(cost__gte=effective.get('min_cost') or 0)
    if effective.get('in_stock_only'):
        rows = rows.filter(_stock_filter())
    rows = rows.filter(_group_filter(effective.get('category_groups') or [],
                                   effective.get('classification_mode', 'auto')))
    before_years = rows.count()
    age = int(effective.get('max_vehicle_age', 20))
    # No source years: keep separately, rather than inventing fitment.
    # A year mentioned in applicability is a candidate, not a verified match.
    years = range(max(1980, date.today().year-age), date.today().year+2)
    year_query = Q(pk__in=[])
    for year in years:
        year_query |= Q(applicability__contains=str(year))
        year_query |= Q(fitments__verified=True, fitments__details__contains=str(year))
    checked_rows = rows.filter(year_query).distinct()
    matched = checked_rows.count()
    unknown_years = max(0, before_years-matched)
    return render(request, 'procurement/selection_rules.html',
                  context(request, form=form, rule=rule, matched=matched,
                          year_unknown=unknown_years, before_years=before_years,
                          group_choices=GROUPS))
