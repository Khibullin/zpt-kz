from django import forms
from .models import Preferences, Draft, Membership, Supplier, Offer, VehicleFitment

from .categories import OMEGA_CATEGORIES


class SelectionFilterForm(forms.Form):
    q = forms.CharField(label='Поиск товара', required=False, max_length=200,
                        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Название, бренд, артикул или применимость'}))
    supplier = forms.ModelChoiceField(label='Поставщик', required=False, queryset=Supplier.objects.none(), empty_label='Все поставщики')
    category = forms.ChoiceField(label='Категория', required=False)
    vehicle_make = forms.ChoiceField(label='Марка автомобиля', required=False)
    vehicle_model = forms.ChoiceField(label='Модель автомобиля', required=False)
    fitment = forms.ChoiceField(label='Применимость', required=False, choices=[('', 'Любая применимость'), ('verified', 'Есть проверенная применимость'), ('unknown', 'Применимость не проверена')])
    availability = forms.ChoiceField(label='Наличие', required=False, choices=[('', 'Любое наличие'), ('in_stock', 'Есть в наличии'), ('unavailable', 'Нет в наличии'), ('unknown', 'Остаток неизвестен')])
    demand = forms.ChoiceField(label='Спрос', required=False, choices=[('', 'Любые признаки спроса'), ('reviews', 'Есть свежие отзывы'), ('sales', 'Есть подтверждённые продажи'), ('unknown', 'Нет свежих подтверждений')])
    max_cost = forms.DecimalField(label='Закупка до, ₸', required=False, min_value=0, max_value=999999999999, max_digits=14, decimal_places=2)
    min_profit = forms.DecimalField(label='Прибыль от, ₸', required=False, min_value=0, max_value=999999999999, max_digits=14, decimal_places=2)
    min_markup = forms.DecimalField(label='Наценка от, %', required=False, min_value=0, max_value=9999999, max_digits=9, decimal_places=2)
    verified = forms.BooleanField(label='Только проверенные совпадения', required=False)
    favorites = forms.BooleanField(label='Только избранное', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supplier'].queryset = Supplier.objects.order_by('name')
        categories = list(OMEGA_CATEGORIES)
        categories += [x for x in Offer.objects.exclude(category='').order_by('category').values_list('category', flat=True).distinct() if x not in categories and x != 'Техобслуживание и сервис']
        self.fields['category'].choices = [('', 'Все категории')] + [(x, x) for x in categories]
        fitments = VehicleFitment.objects.filter(verified=True)
        makes = list(fitments.order_by('make').values_list('make', flat=True).distinct())
        self.fields['vehicle_make'].choices = [('', 'Все марки' if makes else 'Нет проверенных данных о марках')] + [(x, x) for x in makes]
        selected_make = self.data.get('vehicle_make', '') if self.is_bound else ''
        models = list(fitments.filter(make=selected_make).order_by('model').values_list('model', flat=True).distinct()) if selected_make else []
        self.fields['vehicle_model'].choices = [('', 'Все модели' if selected_make else 'Сначала выберите марку')] + [(x, x) for x in models]
        self.vehicle_models = {make: list(fitments.filter(make=make).order_by('model').values_list('model', flat=True).distinct()) for make in makes}



class PreferencesForm(forms.ModelForm):
    class Meta:
        model = Preferences
        fields = ['commission', 'expenses', 'min_profit', 'min_markup']
        labels = {'commission': 'Комиссия, %', 'expenses': 'Все прочие расходы на единицу, ₸',
                  'min_profit': 'Минимальная прибыль, ₸', 'min_markup': 'Минимальная наценка, %'}


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
    kind = forms.ChoiceField(label='Тип файла', choices=[('catalog', 'Прайс поставщика'), ('demand', 'Отчёт спроса Kaspi (подготовленный CSV/XLSX)')])
    supplier = forms.CharField(label='Поставщик', max_length=160)
    file = forms.FileField(label='Прайс CSV или Excel (.xlsx)')
    checked_on = forms.DateField(label='Дата актуальности цен', widget=forms.DateInput(attrs={'type': 'date'}))
    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Максимальный размер — 10 МБ.')
        if not file.name.lower().endswith(('.csv', '.xlsx')):
            raise forms.ValidationError('Поддерживаются CSV и XLSX.')
        return file
