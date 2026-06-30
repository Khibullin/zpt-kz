import re

from django import forms

from .constants import KAZAKHSTAN_CITIES, TRANSPORT_COMPANIES
from .models import Order


class CheckoutForm(forms.Form):
    customer_name = forms.CharField(
        label='Имя',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'checkout-input',
            'placeholder': 'Как к вам обращаться',
            'autocomplete': 'name',
        }),
    )
    customer_phone = forms.CharField(
        label='Телефон',
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'checkout-input checkout-phone-input',
            'placeholder': '+7 (___) ___-__-__',
            'inputmode': 'tel',
            'autocomplete': 'tel',
        }),
    )
    delivery_method = forms.ChoiceField(
        label='Способ доставки',
        choices=Order.DELIVERY_METHOD_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'checkout-radio'}),
        initial=Order.DELIVERY_PICKUP,
    )
    courier_street = forms.CharField(
        label='Улица',
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'class': 'checkout-input', 'placeholder': 'Улица'}),
    )
    courier_house = forms.CharField(
        label='Дом',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'checkout-input', 'placeholder': 'Дом'}),
    )
    courier_apartment = forms.CharField(
        label='Квартира',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'checkout-input', 'placeholder': 'Квартира / офис'}),
    )
    kz_city = forms.ChoiceField(
        label='Город',
        choices=[('', 'Выберите город')] + [(city, city) for city in KAZAKHSTAN_CITIES],
        required=False,
        widget=forms.Select(attrs={'class': 'checkout-input'}),
    )
    transport_company = forms.ChoiceField(
        label='Транспортная компания',
        choices=[('', 'Выберите ТК')] + list(TRANSPORT_COMPANIES),
        required=False,
        widget=forms.Select(attrs={'class': 'checkout-input'}),
    )

    def clean_customer_phone(self):
        phone = re.sub(r'\D', '', self.cleaned_data.get('customer_phone', ''))
        if phone.startswith('8'):
            phone = '7' + phone[1:]
        if not phone.startswith('7'):
            phone = '7' + phone
        if len(phone) != 11:
            raise forms.ValidationError('Введите корректный номер телефона в формате +7.')
        return f'+{phone}'

    def clean(self):
        cleaned_data = super().clean()
        delivery_method = cleaned_data.get('delivery_method')

        if delivery_method == Order.DELIVERY_COURIER:
            if not cleaned_data.get('courier_street'):
                self.add_error('courier_street', 'Укажите улицу.')
            if not cleaned_data.get('courier_house'):
                self.add_error('courier_house', 'Укажите номер дома.')

        if delivery_method == Order.DELIVERY_KZ:
            if not cleaned_data.get('kz_city'):
                self.add_error('kz_city', 'Выберите город.')
            if not cleaned_data.get('transport_company'):
                self.add_error('transport_company', 'Выберите транспортную компанию.')

        return cleaned_data

    def build_delivery_address(self):
        delivery_method = self.cleaned_data['delivery_method']
        if delivery_method == Order.DELIVERY_PICKUP:
            return {'type': delivery_method}
        if delivery_method == Order.DELIVERY_COURIER:
            return {
                'type': delivery_method,
                'street': self.cleaned_data['courier_street'],
                'house': self.cleaned_data['courier_house'],
                'apartment': self.cleaned_data.get('courier_apartment', ''),
            }
        return {
            'type': delivery_method,
            'city': self.cleaned_data['kz_city'],
            'transport_company': self.cleaned_data['transport_company'],
            'transport_company_label': dict(TRANSPORT_COMPANIES).get(
                self.cleaned_data['transport_company'],
                '',
            ),
        }
