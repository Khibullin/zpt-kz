from django import forms
from django.utils import timezone
from core.models import RequestOffer
from core.services.request_connections import telegram_contact


class SellerLoginForm(forms.Form):
    phone = forms.CharField(label='Телефон / WhatsApp', max_length=30)
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)


class SellerJoinForm(SellerLoginForm):
    name = forms.CharField(label='Название магазина', max_length=255)
    city = forms.CharField(label='Город', max_length=100)
    password_confirm = forms.CharField(label='Повторите пароль', widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('password_confirm'):
            self.add_error('password_confirm', 'Пароли не совпадают.')
        return data


class BuyerPermissionsForm(forms.Form):
    requested_quantity = forms.IntegerField(required=False, min_value=1, label='Количество деталей')
    buyer_priority = forms.ChoiceField(choices=[('balanced', 'Подходящие условия'), ('price', 'Выгоднее по цене'),
        ('urgent', 'Нужно быстрее'), ('original', 'Только оригинал')], label='Что важнее')
    buyer_budget = forms.IntegerField(required=False, min_value=1, label='Бюджет на всё количество с доставкой, ₸')
    public_consent = forms.BooleanField(required=False, label='Разрешаю публично размещать обезличенную заявку без телефона, VIN и фотографий')
    contact_consent = forms.BooleanField(required=False, label='Разрешаю допущенным продавцам связаться со мной по этой заявке')
    telegram = forms.CharField(required=False, max_length=100, label='Мой Telegram username (необязательно)')

    def clean_telegram(self):
        value = self.cleaned_data['telegram'].strip()
        if value and not telegram_contact(value):
            raise forms.ValidationError('Укажите username личного Telegram-аккаунта.')
        return value


class OfferForm(forms.ModelForm):
    class Meta:
        model = RequestOffer
        fields = ['article', 'manufacturer', 'is_original', 'condition', 'quantity', 'price', 'delivery_price',
                  'delivery_days', 'availability', 'warranty', 'fitment_confirmed', 'expires_at']
        widgets = {'expires_at': forms.DateTimeInput(attrs={'type': 'datetime-local'})}

    def clean_expires_at(self):
        value = self.cleaned_data['expires_at']
        if value <= timezone.now():
            raise forms.ValidationError('Укажите будущую дату актуальности.')
        return value
