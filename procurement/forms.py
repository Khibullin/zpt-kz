from django import forms
from .models import Preferences, Draft, Membership, Supplier, Offer


class SelectionFilterForm(forms.Form):
    q = forms.CharField(label='Поиск товара', required=False, max_length=200,
                        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Название, бренд, артикул или применимость'}))
    supplier = forms.ModelChoiceField(label='Поставщик', required=False, queryset=Supplier.objects.none(), empty_label='Все поставщики')
    category = forms.ChoiceField(label='Категория', required=False)
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
        self.fields['category'].choices = [('', 'Все категории')] + [(x, x) for x in Offer.objects.exclude(category='').order_by('category').values_list('category', flat=True).distinct()]


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
