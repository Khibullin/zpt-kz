from django import forms
from .models import Preferences, Draft, Membership


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
