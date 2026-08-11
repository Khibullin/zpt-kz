from django import forms

from .models import (
    SellerProfile,
    Product,
    Country,
    Brand,
    CarModel,
    DEFAULT_SELLER_WORK_HOURS,
    DEFAULT_SELLER_DELIVERY_INFO,
)

STORE_ADDRESS_PLACEHOLDER = 'г. Алматы, ул. Примерная, 1'
PICKUP_ADDRESS_PLACEHOLDER = 'г. Алматы, склад / пункт выдачи'


class SellerPickupFieldsMixin:
    """Shared pickup address UX for seller register/edit forms."""

    def _setup_pickup_fields(self):
        self.fields['address'].label = 'Адрес магазина / офиса'
        self.fields['address'].required = False
        self.fields['address'].widget.attrs.setdefault(
            'placeholder',
            STORE_ADDRESS_PLACEHOLDER,
        )

        self.fields['pickup_same_as_store'].required = False
        self.fields['pickup_available'].required = False
        self.fields['pickup_address'].required = False
        self.fields['pickup_address'].label = 'Адрес самовывоза'
        self.fields['pickup_address'].widget.attrs.setdefault(
            'placeholder',
            PICKUP_ADDRESS_PLACEHOLDER,
        )

        if not self.is_bound:
            self.initial.setdefault('pickup_same_as_store', True)
            self.initial.setdefault('pickup_available', True)

    def clean_pickup_fields(self, cleaned_data):
        address = (cleaned_data.get('address') or '').strip()
        cleaned_data['address'] = address

        pickup_same_as_store = bool(cleaned_data.get('pickup_same_as_store'))
        pickup_available = bool(cleaned_data.get('pickup_available'))
        pickup_address = (cleaned_data.get('pickup_address') or '').strip()

        if pickup_same_as_store:
            pickup_address = address

        cleaned_data['pickup_same_as_store'] = pickup_same_as_store
        cleaned_data['pickup_available'] = pickup_available
        cleaned_data['pickup_address'] = pickup_address

        if pickup_available and not pickup_same_as_store and not pickup_address:
            self.add_error(
                'pickup_address',
                'Укажите адрес самовывоза или отметьте совпадение с адресом магазина.',
            )

        return cleaned_data


class SellerRegisterForm(SellerPickupFieldsMixin, forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, label='Пароль')

    class Meta:
        model = SellerProfile

        fields = [
            'name',
            'phone',
            'city',
            'address',
            'pickup_same_as_store',
            'pickup_address',
            'pickup_available',
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
            'address': 'Адрес магазина / офиса',
            'pickup_same_as_store': 'Адрес самовывоза совпадает с адресом магазина',
            'pickup_address': 'Адрес самовывоза',
            'pickup_available': 'Самовывоз доступен',
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
                'placeholder': STORE_ADDRESS_PLACEHOLDER,
            }),

            'pickup_address': forms.TextInput(attrs={
                'placeholder': PICKUP_ADDRESS_PLACEHOLDER,
            }),

            'pickup_same_as_store': forms.CheckboxInput(attrs={
                'class': 'seller-pickup-same-checkbox',
            }),

            'pickup_available': forms.CheckboxInput(attrs={
                'class': 'seller-pickup-available-checkbox',
            }),

            'work_hours': forms.TextInput(attrs={
                'placeholder': DEFAULT_SELLER_WORK_HOURS,
            }),

            'delivery_info': forms.Textarea(attrs={
                'placeholder': DEFAULT_SELLER_DELIVERY_INFO,
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        if 'logo' in self.fields:
            self.fields['logo'].required = False
        for optional_field in ('instagram', 'website'):
            if optional_field in self.fields:
                self.fields[optional_field].required = False
        self._setup_pickup_fields()
        if not self.is_bound and not getattr(self.instance, 'pk', None):
            self.initial.setdefault('work_hours', DEFAULT_SELLER_WORK_HOURS)
            self.initial.setdefault('delivery_info', DEFAULT_SELLER_DELIVERY_INFO)

    def clean_instagram(self):
        instagram = (self.cleaned_data.get('instagram') or '').strip()
        if not instagram:
            return ''
        if '://' not in instagram and not instagram.startswith('//'):
            instagram = f'https://{instagram.lstrip("/")}'
        return instagram

    def clean_website(self):
        website = (self.cleaned_data.get('website') or '').strip()
        return website

    def clean(self):
        cleaned_data = super().clean()
        name = (cleaned_data.get('name') or '').strip()
        if not name:
            self.add_error('name', 'Укажите название маркета.')
        else:
            cleaned_data['name'] = name
        return self.clean_pickup_fields(cleaned_data)


class SellerProfileForm(SellerPickupFieldsMixin, forms.ModelForm):
    class Meta:
        model = SellerProfile

        fields = [
            'name',
            'phone',
            'city',
            'address',
            'pickup_same_as_store',
            'pickup_address',
            'pickup_available',
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
            'address': 'Адрес магазина / офиса',
            'pickup_same_as_store': 'Адрес самовывоза совпадает с адресом магазина',
            'pickup_address': 'Адрес самовывоза',
            'pickup_available': 'Самовывоз доступен',
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
                'placeholder': STORE_ADDRESS_PLACEHOLDER,
            }),

            'pickup_address': forms.TextInput(attrs={
                'placeholder': PICKUP_ADDRESS_PLACEHOLDER,
            }),

            'pickup_same_as_store': forms.CheckboxInput(attrs={
                'class': 'seller-pickup-same-checkbox',
            }),

            'pickup_available': forms.CheckboxInput(attrs={
                'class': 'seller-pickup-available-checkbox',
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        if 'logo' in self.fields:
            self.fields['logo'].required = False
        self._setup_pickup_fields()

    def clean(self):
        cleaned_data = super().clean()
        name = (cleaned_data.get('name') or '').strip()
        if not name:
            self.add_error('name', 'Укажите название маркета.')
        else:
            cleaned_data['name'] = name
        return self.clean_pickup_fields(cleaned_data)


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
