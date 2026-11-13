from decimal import Decimal
from django import forms
from .models import KaspiPricingRule


class CardMaterialForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    title = forms.CharField(label='Название товара', max_length=500)
    description = forms.CharField(label='Описание для Kaspi', required=False, max_length=1024, widget=forms.Textarea(attrs={'rows': 5}))
    category = forms.CharField(label='Код категории Kaspi', required=False, max_length=512,
        help_text='Выберите код из справочника Kaspi. Категория поставщика не подставляется вместо него.')
    attributes = forms.CharField(label='Характеристики', required=False, widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Каждая строка: характеристика: значение. Только подтверждённые сведения.')
    applicability = forms.CharField(label='Применимость: марка, модель, годы, двигатель', required=False, widget=forms.Textarea(attrs={'rows': 5}))
    applicability_source = forms.URLField(label='Источник применимости', required=False, max_length=2048)
    verified = forms.BooleanField(label='Проверены требования категории Kaspi, характеристики, фотографии и комплектность', required=False)

    def clean_attributes(self):
        result = {}
        for line in self.cleaned_data['attributes'].splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition(':')
            if not separator or not key.strip() or not value.strip():
                raise forms.ValidationError('Укажите характеристику и значение через двоеточие.')
            if len(key.strip()) > 256 or len(value.strip()) > 256 or len(result) >= 256:
                raise forms.ValidationError('Слишком длинные или многочисленные характеристики.')
            result[key.strip()] = value.strip()
        return result


class CardPriceForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    mode = forms.ChoiceField(label='Режим цены', choices=[('algorithm', 'По алгоритму'), ('manual', 'Вручную')])
    amount = forms.DecimalField(label='Наша цена, ₸', required=False, min_value=Decimal('0.01'), max_digits=14, decimal_places=2)

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'manual' and data.get('amount') is None:
            self.add_error('amount', 'Укажите цену для ручного режима.')
        return data


class PricingRuleForm(forms.ModelForm):
    value = forms.DecimalField(label='Сумма снижения, ₸', max_digits=14, decimal_places=2,
        min_value=Decimal('0'), localize=True,
        widget=forms.TextInput(attrs={'inputmode': 'decimal', 'data-pricing-value': ''}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        method = self.data.get('method') if self.is_bound else self.initial.get('method', 'minimum_minus_amount')
        self.fields['value'].label = {
            'minimum_minus_amount': 'Сумма снижения, ₸',
            'minimum_minus_percent': 'Процент снижения, %',
            'cost_plus_percent': 'Процент наценки, %',
        }.get(method, 'Сумма снижения, ₸')
        self.fields['value'].help_text = 'Введите число, например 3 или 2,5. Единица указана в названии поля.'

    class Meta:
        model = KaspiPricingRule
        fields = ['enabled', 'method', 'value', 'min_profit']
        labels = {'enabled': 'Включить расчёт по этому алгоритму', 'method': 'Способ расчёта',
            'value': 'Сумма или процент по выбранному способу', 'min_profit': 'Минимальная прибыль с единицы, ₸'}

    def clean(self):
        data = super().clean()
        value = data.get('value')
        if value is not None and value < 0:
            self.add_error('value', 'Значение не может быть отрицательным.')
        if value is not None and data.get('method') == 'minimum_minus_percent' and value >= 100:
            self.add_error('value', 'Снижение цены должно быть меньше 100%.')
        if data.get('min_profit') is not None and data['min_profit'] < 0:
            self.add_error('min_profit', 'Минимальная прибыль не может быть отрицательной.')
        return data
