from django import forms
from django.core.exceptions import ValidationError

from .models import SellerProfile, Product, Country, Brand, CarModel


class SellerRegisterForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, label='Пароль')

    class Meta:
        model = SellerProfile

        fields = [
            'name',
            'phone',
            'city',
            'address',
            'work_hours',
            'delivery_info',
            'instagram',
            'website',
            'description',
            'logo',
        ]

        labels = {
            'name': 'Название маркета',
            'phone': 'Телефон / WhatsApp',
            'city': 'Город',
            'address': 'Адрес склада',
            'work_hours': 'График работы',
            'delivery_info': 'Доставка и оплата',
            'instagram': 'Instagram',
            'website': 'Сайт',
            'description': 'Описание маркета',
            'logo': 'Логотип маркета',
        }

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'Например: Auto Parts Алматы'
            }),

            'phone': forms.TextInput(attrs={
                'placeholder': 'Например: 77713607040'
            }),

            'city': forms.TextInput(attrs={
                'placeholder': 'Например: Алматы'
            }),

            'address': forms.TextInput(attrs={
                'placeholder': 'г. Алматы, ул. Райымбека, 212б, корпус 3, бокс 5'
            }),

            'work_hours': forms.TextInput(attrs={
                'placeholder': 'Пн–Сб: 09:00 – 19:00, Вс: выходной'
            }),

            'delivery_info': forms.Textarea(attrs={
                'placeholder': 'Самовывоз, курьер, отправка в регионы',
                'rows': 3
            }),

            'instagram': forms.TextInput(attrs={
                'placeholder': 'Например: instagram.com/autoparts_kz'
            }),

            'website': forms.URLInput(attrs={
                'placeholder': 'Например: https://site.kz'
            }),

            'description': forms.Textarea(attrs={
                'placeholder': 'Кратко расскажите о маркете',
                'rows': 4
            }),
        }


class SellerProfileForm(forms.ModelForm):
    class Meta:
        model = SellerProfile

        fields = [
            'name',
            'phone',
            'city',
            'address',
            'work_hours',
            'delivery_info',
            'instagram',
            'website',
            'description',
            'logo',
        ]

        labels = {
            'name': 'Название маркета',
            'phone': 'Телефон / WhatsApp',
            'city': 'Город',
            'address': 'Адрес склада',
            'work_hours': 'График работы',
            'delivery_info': 'Доставка и оплата',
            'instagram': 'Instagram',
            'website': 'Сайт',
            'description': 'Описание маркета',
            'logo': 'Логотип маркета',
        }

        widgets = {
            'name': forms.TextInput(attrs={
                'placeholder': 'Например: Auto Parts Алматы'
            }),

            'phone': forms.TextInput(attrs={
                'placeholder': 'Например: 77713607040'
            }),

            'city': forms.TextInput(attrs={
                'placeholder': 'Например: Алматы'
            }),

            'address': forms.TextInput(attrs={
                'placeholder': 'г. Алматы, ул. Райымбека, 212б, корпус 3, бокс 5'
            }),

            'work_hours': forms.TextInput(attrs={
                'placeholder': 'Пн–Сб: 09:00 – 19:00, Вс: выходной'
            }),

            'delivery_info': forms.Textarea(attrs={
                'placeholder': 'Самовывоз, курьер, отправка в регионы',
                'rows': 3
            }),

            'instagram': forms.TextInput(attrs={
                'placeholder': 'Например: instagram.com/autoparts_kz'
            }),

            'website': forms.URLInput(attrs={
                'placeholder': 'Например: https://site.kz'
            }),

            'description': forms.Textarea(attrs={
                'placeholder': 'Кратко расскажите о маркете',
                'rows': 4
            }),
        }

class ProductForm(forms.ModelForm):
    country = forms.ModelChoiceField(
        queryset=Country.objects.all().order_by('name'),
        required=False,
        label='Страна'
    )


    selected_models = forms.ModelMultipleChoiceField(
        queryset=CarModel.objects.none(),
        required=False,
        label='Дополнительно подходит к моделям',
        widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Product
        fields = [
            'country',
            'brand',
            'car_model',
            'selected_models',
            'category',
            'title',
            'article',
            'price',
            'price_on_request',
            'condition',
            'status',
            'main_image',
            'compatibility',
            'description',
        ]
        labels = {
            'country': 'Страна',
            'brand': 'Марка',
            'car_model': 'Модель',
            'selected_models': 'Дополнительно подходит к моделям',
            'category': 'Категория',
            'title': 'Название товара',
            'article': 'Артикул',
            'price': 'Цена',
            'price_on_request': 'Цена по запросу',
            'condition': 'Состояние',
            'status': 'Статус',
            'main_image': 'Главное фото',
            'compatibility': 'Подходит для',
            'description': 'Описание',
        }
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Например: Амортизатор передний Camry 40'
            }),
            'article': forms.TextInput(attrs={
                'placeholder': 'Если есть артикул — укажите'
            }),
            'price': forms.NumberInput(attrs={
                'placeholder': 'Цена в тенге'
            }),
            'compatibility': forms.Textarea(attrs={
                'placeholder': 'Например: Toyota Camry 40, 2006–2011',
                'rows': 3
            }),
            'description': forms.Textarea(attrs={
                'placeholder': 'Опишите состояние, оригинал или аналог, комплектность',
                'rows': 5
            }),
            'price_on_request': forms.CheckboxInput(attrs={
                'id': 'id_price_on_request',
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        price_on_request = cleaned_data.get('price_on_request')
        price = cleaned_data.get('price')

        if price_on_request:
            cleaned_data['price'] = None
        elif not price or price <= 0:
            self.add_error(
                'price',
                'Укажите цену больше 0 или включите «Цена по запросу».',
            )

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['brand'].queryset = Brand.objects.none()
        self.fields['car_model'].queryset = CarModel.objects.none()

        country_id = None
        brand_id = None

        if self.data.get('country'):
            country_id = self.data.get('country')
        elif self.initial.get('country'):
            country_id = self.initial.get('country')
        elif self.instance.pk and self.instance.brand:
            country_id = self.instance.brand.country_id

        if country_id:
            self.fields['brand'].queryset = Brand.objects.filter(
                country_id=country_id
            ).order_by('name')

        if self.data.get('brand'):
            brand_id = self.data.get('brand')
        elif self.initial.get('brand'):
            brand_id = self.initial.get('brand')
        elif self.instance.pk and self.instance.brand:
            brand_id = self.instance.brand_id

        if brand_id:
            self.fields['car_model'].queryset = CarModel.objects.filter(
                brand_id=brand_id
            ).order_by('name')


        if brand_id:
            self.fields['selected_models'].queryset = CarModel.objects.filter(
                brand_id=brand_id
            ).order_by('name')
        else:
            self.fields['selected_models'].queryset = CarModel.objects.none()