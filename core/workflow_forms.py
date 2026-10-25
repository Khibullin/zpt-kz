from django import forms
from core.models import Request, Seller, RequestSellerReview
from core.services.seller_identity import normalize_seller_whatsapp, normalize_seller_city, SellerIdentityError


class ClaimPhoneForm(forms.Form):
    phone = forms.CharField(label='Телефон магазина', max_length=30, widget=forms.TextInput(attrs={'type': 'tel', 'placeholder': '+7 (777) 123-45-67', 'inputmode': 'tel', 'autocomplete': 'tel', 'data-kz-phone': '1'}))

    def clean_phone(self):
        phone = normalize_seller_whatsapp(self.cleaned_data['phone'])
        if not phone or len(phone) != 11:
            raise forms.ValidationError('Укажите корректный телефон магазина.')
        return phone


class NewShopForm(ClaimPhoneForm):
    field_order = ['name', 'city', 'phone', 'brand', 'category', 'new_parts', 'used_parts', 'consent']
    name = forms.CharField(label='Название магазина', max_length=255)
    city = forms.CharField(label='Город', max_length=100)
    brand = forms.CharField(label='Марки автомобилей', max_length=100, required=False)
    category = forms.CharField(label='Категории запчастей', max_length=100, required=False)
    new_parts = forms.BooleanField(label='Новые', required=False)
    used_parts = forms.BooleanField(label='Б/у', required=False)
    consent = forms.BooleanField(label='Согласен с условиями сервиса и обработкой персональных данных')

    def clean_city(self):
        try:
            return normalize_seller_city(self.cleaned_data['city'], required=True)
        except SellerIdentityError:
            raise forms.ValidationError('Выберите город Казахстана.')


class BuyerEditForm(forms.ModelForm):
    photo = forms.ImageField(label='Добавить фотографию', required=False)

    def clean_photo(self):
        image = self.cleaned_data.get('photo')
        if image and image.size > 8 * 1024 * 1024:
            raise forms.ValidationError('Размер фотографии — до 8 МБ.')
        return image

    class Meta:
        model = Request
        fields = ['brand', 'model', 'year', 'city', 'description']
        labels = {'brand': 'Марка', 'model': 'Модель', 'year': 'Год', 'city': 'Город', 'description': 'Что требуется'}
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class ShopEditForm(forms.ModelForm):
    class Meta:
        model = Seller
        fields = ['name', 'city', 'address', 'directory_description', 'directory_photo', 'brand', 'category', 'website']
        labels = {'name': 'Название магазина', 'city': 'Город', 'address': 'Адрес', 'directory_description': 'Ассортимент и наличие', 'directory_photo': 'Фотография магазина', 'brand': 'Марки автомобилей', 'category': 'Категории запчастей', 'website': 'Сайт'}
        widgets = {'directory_description': forms.Textarea(attrs={'rows': 4})}


class ReviewForm(forms.ModelForm):
    class Meta:
        model = RequestSellerReview
        fields = ['rating', 'text', 'public_consent']
        labels = {'rating': 'Оцените продавца', 'text': 'Отзыв (необязательно)', 'public_consent': 'Разрешаю опубликовать отзыв без моего имени и контактов'}
        widgets = {'rating': forms.RadioSelect(choices=[(x, '★' * x) for x in range(1, 6)]), 'text': forms.Textarea(attrs={'rows': 4})}
