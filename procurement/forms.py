from django import forms
from django.forms import formset_factory
from .models import Preferences, Draft, Membership, Supplier, Offer, VehicleFitment, PurchaseOrder

from .filter_options import supplier_options, supplier_years, supplier_generations
from .categories import OMEGA_CATEGORIES
from core.models import Country
from .vehicle_countries import country_makes


class SelectionFilterForm(forms.Form):
    q = forms.CharField(label='Поиск товара', required=False, max_length=200,
                        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Название, бренд, артикул или применимость'}))
    supplier = forms.ModelChoiceField(label='Поставщик', required=False, queryset=Supplier.objects.none(), empty_label='Все поставщики')
    category = forms.ChoiceField(label='Категория', required=False)
    vehicle_country = forms.ModelChoiceField(label='Страна автомобиля', required=False, queryset=Country.objects.none(), empty_label='Все страны')
    vehicle_make = forms.ChoiceField(label='Марка автомобиля', required=False)
    vehicle_model = forms.ChoiceField(label='Модель автомобиля', required=False)
    vehicle_generation = forms.ChoiceField(label='Поколение автомобиля', required=False)
    vehicle_year = forms.TypedChoiceField(label='Год выпуска автомобиля', required=False, coerce=int, empty_value=None)
    year_mode = forms.ChoiceField(label='Годы выпуска', required=False, choices=[('', 'Все годы'), ('selected', 'Выбрать годы'), ('range', 'Интервал')])
    vehicle_years = forms.TypedMultipleChoiceField(label='Выберите годы', required=False, coerce=int, widget=forms.CheckboxSelectMultiple)
    year_from = forms.TypedChoiceField(label='С', required=False, coerce=int, empty_value=None)
    year_to = forms.TypedChoiceField(label='По', required=False, coerce=int, empty_value=None)
    fitment = forms.ChoiceField(label='Применимость', required=False, choices=[('', 'Любая применимость'), ('verified', 'Есть проверенная применимость'), ('unknown', 'Применимость не проверена')])
    availability = forms.ChoiceField(label='Наличие', required=False, choices=[('', 'Любое наличие'), ('in_stock', 'Есть в наличии'), ('unavailable', 'Нет в наличии'), ('unknown', 'Остаток неизвестен')])
    demand = forms.ChoiceField(label='Спрос', required=False, choices=[('', 'Любые признаки спроса'), ('reviews', 'Есть свежие отзывы'), ('sales', 'Есть подтверждённые продажи'), ('unknown', 'Нет свежих подтверждений')])
    kaspi_seller_count = forms.IntegerField(label='Количество продавцов на Kaspi', required=False, min_value=0, max_value=100000, widget=forms.NumberInput(attrs={'list': 'kaspi-seller-counts', 'placeholder': 'Все', 'step': '1'}))
    seller_mode = forms.ChoiceField(label='Количество продавцов на Kaspi', required=False, choices=[('', 'Все'), ('max', 'До'), ('exact', 'Ровно'), ('range', 'Интервал')])
    seller_min = forms.IntegerField(label='От', required=False, min_value=0, max_value=100000)
    seller_max = forms.IntegerField(label='До', required=False, min_value=0, max_value=100000)
    kaspi_status = forms.MultipleChoiceField(label='Статус на Kaspi', required=False, widget=forms.CheckboxSelectMultiple, choices=[('sellers', 'Карточка есть — есть продавцы Kaspi'), ('empty', 'Карточка есть — нет продавцов Kaspi'), ('missing', 'Карточка Kaspi не найдена'), ('pending', 'Проверка Kaspi не завершена')])
    sort = forms.ChoiceField(label='Сортировка', required=False, choices=[('', 'Доходность — сначала наибольшая'), ('profit', 'Прибыль — сначала наибольшая')])
    brand = forms.CharField(label='Бренд детали', required=False, max_length=160)
    max_cost = forms.DecimalField(label='Закупка до, ₸', required=False, min_value=0, max_value=999999999999, max_digits=14, decimal_places=2)
    min_profit = forms.DecimalField(label='Прибыль от, ₸', required=False, min_value=0, max_value=999999999999, max_digits=14, decimal_places=2)
    min_markup = forms.DecimalField(label='Доходность после комиссии от, %', required=False, min_value=0, max_value=9999999, max_digits=9, decimal_places=2)
    verified = forms.BooleanField(label='Только проверенные совпадения', required=False)
    favorites = forms.BooleanField(label='Только избранное', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.order_by('name')
        selected_supplier = self.data.get('supplier', '') if self.is_bound else ''
        supplier_id = int(selected_supplier) if str(selected_supplier).isdigit() else None
        category_values, models_by_make = supplier_options(supplier_id)
        categories = list(OMEGA_CATEGORIES) if not selected_supplier else []
        for value in category_values:
            value = 'Тех обслуживание и сервис' if value == 'Техобслуживание и сервис' else value
            if value not in categories:
                categories.append(value)
        self.fields['category'].choices = [('', 'Все категории')] + [(x, x) for x in categories]
        self.fields['vehicle_country'].queryset = Country.objects.order_by('name')
        selected_country = self.data.get('vehicle_country', '') if self.is_bound else ''
        allowed_makes = None
        if str(selected_country).isdigit():
            allowed_makes = set(country_makes(selected_country))
        self.vehicle_models = {make: models for make, models in models_by_make.items()
                               if allowed_makes is None or make in allowed_makes}
        makes = list(self.vehicle_models)
        self.fields['vehicle_make'].choices = [('', 'Все марки' if makes else 'Нет проверенных данных о марках')] + [(x, x) for x in makes]
        selected_make = self.data.get('vehicle_make', '') if self.is_bound else ''
        models = self.vehicle_models.get(selected_make, [])
        self.fields['vehicle_model'].choices = [('', 'Все модели' if selected_make else 'Сначала выберите марку')] + [(x, x) for x in models]
        selected_model = self.data.get('vehicle_model', '') if self.is_bound else ''
        generations = supplier_generations(supplier_id, selected_make, selected_model)
        self.fields['vehicle_generation'].choices = [('', 'Все поколения' if selected_make and selected_model else 'Сначала выберите модель')] + [(x, x) for x in sorted(generations)]
        years = supplier_years(supplier_id)
        choices = [(str(x), str(x)) for x in years]
        self.fields['vehicle_year'].choices = [('', 'Все годы')] + choices
        self.fields['vehicle_years'].choices = choices
        for field in ('year_from', 'year_to'):
            self.fields[field].choices = [('', 'Выберите год')] + choices
        if self.is_bound and 'year_mode' not in self.data and self.data.get('vehicle_year'):
            self.data = self.data.copy()
            self.data['year_mode'] = 'selected'
            if hasattr(self.data, 'setlist'):
                self.data.setlist('vehicle_years', [self.data['vehicle_year']])
            else:
                self.data['vehicle_years'] = [self.data['vehicle_year']]

        if self.is_bound and 'seller_mode' not in self.data and self.data.get('kaspi_seller_count') not in (None, ''):
            self.data = self.data.copy()
            self.data['seller_mode'] = 'exact'

    def clean(self):
        data = super().clean()
        seller_mode = data.get('seller_mode')
        low = high = None
        if seller_mode == 'exact':
            low = high = data.get('kaspi_seller_count')
            if high is None:
                self.add_error('kaspi_seller_count', 'Укажите количество продавцов.')
        elif seller_mode == 'max':
            low, high = 0, data.get('seller_max')
            if high is None:
                self.add_error('seller_max', 'Укажите максимальное количество.')
        elif seller_mode == 'range':
            low, high = data.get('seller_min'), data.get('seller_max')
            if low is None or high is None:
                self.add_error('seller_min', 'Укажите обе границы интервала.')
            elif low > high:
                self.add_error('seller_max', 'Верхняя граница должна быть не меньше нижней.')
        data['seller_bounds'] = (low, high) if low is not None and high is not None else None
        mode = data.get('year_mode')
        data['selected_years'] = []
        if mode == 'selected':
            data['selected_years'] = data.get('vehicle_years', [])
            if not data['selected_years']:
                self.add_error('vehicle_years', 'Выберите хотя бы один год.')
        elif mode == 'range':
            start, end = data.get('year_from'), data.get('year_to')
            if start is None or end is None:
                self.add_error('year_from', 'Укажите начало и конец интервала.')
            elif start > end:
                self.add_error('year_to', 'Конец интервала должен быть не раньше начала.')
            else:
                data['selected_years'] = list(range(start, end + 1))
        data['vehicle_year'] = None
        return data




class PreferencesForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older open settings forms may omit newly introduced controls.
        for name in ('best_min_profit', 'no_sellers_days'):
            self.fields[name].required = False

    def clean_best_min_profit(self):
        return self.cleaned_data.get('best_min_profit') or self.instance.best_min_profit

    def clean_no_sellers_days(self):
        return self.cleaned_data.get('no_sellers_days') or self.instance.no_sellers_days

    class Meta:
        model = Preferences
        fields = ['commission', 'expenses', 'difference_min_profit', 'best_min_profit', 'no_sellers_days']
        labels = {'commission': 'Комиссия Kaspi без НДС, %', 'expenses': 'Все расходы на единицу, включая упаковку 250 ₸',
                  'difference_min_profit': 'Минимальная прибыль для максимальной разницы, ₸ (необязательно)',
                  'best_min_profit': 'Минимальная прибыль выгодной закупки, ₸ (от 1 000)',
                  'no_sellers_days': 'Срок актуальности проверки продавцов, дней (1–30)'}


class DraftForm(forms.ModelForm):
    def clean_characteristics(self):
        data = self.cleaned_data['characteristics']
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise forms.ValidationError('Характеристики должны быть JSON-объектом: {"параметр": "значение"}.')
        return data

    class Meta:
        model = Draft
        fields = ['title', 'description', 'photo_url', 'category', 'characteristics']
        labels = {'title': 'Название', 'description': 'Описание', 'photo_url': 'Ссылка на фото',
                  'category': 'Категория Kaspi', 'characteristics': 'Характеристики (JSON)'}


class UploadForm(forms.Form):
    kind = forms.ChoiceField(label='Тип файла', choices=[('catalog', 'Прайс поставщика'), ('demand', 'Отчёт спроса Kaspi (подготовленный CSV/XLSX)'), ('enrichment', 'Применимость и категории Omega (JSON)'), ('catalogue_fitments', 'Проверенная применимость из каталогов (JSON)'), ('competitors', 'Продавцы Kaspi (JSON)'), ('matches', 'Сопоставление Kaspi: артикул → название → бренд (JSON)'), ('card_searches', 'Карточка Kaspi не найдена: результаты поиска (JSON)')])
    supplier = forms.CharField(label='Поставщик', max_length=160)
    file = forms.FileField(label='Файл CSV, Excel или JSON применимости')
    checked_on = forms.DateField(label='Дата актуальности данных', widget=forms.DateInput(attrs={'type': 'date'}))
    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > 50 * 1024 * 1024:
            raise forms.ValidationError('Максимальный размер — 50 МБ.')
        if not file.name.lower().endswith(('.csv', '.xlsx', '.json')):
            raise forms.ValidationError('Поддерживаются CSV, XLSX и JSON применимости.')
        return file


class PurchaseConfirmationForm(forms.Form):
    supplier_order_number = forms.CharField(label='Номер заказа у поставщика (если есть)', max_length=100, required=False)
    comment = forms.CharField(label='Комментарий', max_length=3000, required=False, widget=forms.Textarea(attrs={'rows': 3}))
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    selection_token = forms.CharField(widget=forms.HiddenInput)


class PurchaseEditForm(forms.ModelForm):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    invoice_file = forms.FileField(label='Прикрепить счёт', required=False)
    waybill_file = forms.FileField(label='Прикрепить накладную', required=False)

    def clean(self):
        data = super().clean()
        for field in ('invoice_file', 'waybill_file'):
            file = data.get(field)
            if file and (file.size > 5 * 1024 * 1024 or not file.name.lower().endswith(('.pdf', '.xlsx', '.jpg', '.jpeg', '.png'))):
                self.add_error(field, 'PDF, XLSX, JPG или PNG, не более 5 МБ.')
        return data

    class Meta:
        model = PurchaseOrder
        fields = ['status', 'supplier_order_number', 'invoice_number', 'waybill_number', 'comment']
        labels = {'status': 'Статус', 'supplier_order_number': 'Номер заказа у поставщика',
                  'invoice_number': 'Номер счёта', 'waybill_number': 'Номер накладной', 'comment': 'Комментарий'}
        widgets = {'comment': forms.Textarea(attrs={'rows': 3})}


class PurchaseLineForm(forms.Form):
    quantity = forms.IntegerField(label='Количество', min_value=0, max_value=100000)
    cost = forms.DecimalField(label='Цена, ₸', min_value=0, max_value=999999999999, max_digits=14, decimal_places=2)


PurchaseLineFormSet = formset_factory(PurchaseLineForm, extra=0, max_num=20000, absolute_max=20000, validate_max=True)
