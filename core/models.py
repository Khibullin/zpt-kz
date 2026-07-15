import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text


TRANSPORT_CHOICES = [
    ('car', 'Легковые'),
    ('truck', 'Грузовые'),
]


SELLER_TYPE_CHOICES = [
    ('seller', 'Продавец запчастей'),
    ('service', 'Сервис / СТО'),
    ('both', 'Продавец + сервис'),
]

REQUEST_SEARCH_SCOPE_CHOICES = [
    ('city', 'Только мой город'),
    ('kazakhstan', 'Весь Казахстан'),
    ('custom', 'Выбрать города'),
]

BUYER_CONTACT_STATUS_ACTIVE = 'active'
BUYER_CONTACT_STATUS_INVALID_PHONE = 'invalid_phone'
BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE = 'whatsapp_unavailable'
BUYER_CONTACT_STATUS_UNSUBSCRIBED = 'unsubscribed'
BUYER_CONTACT_STATUS_BLOCKED = 'blocked'

BUYER_CONTACT_STATUS_CHOICES = [
    (BUYER_CONTACT_STATUS_ACTIVE, 'Активный'),
    (BUYER_CONTACT_STATUS_INVALID_PHONE, 'Некорректный телефон'),
    (BUYER_CONTACT_STATUS_WHATSAPP_UNAVAILABLE, 'WhatsApp недоступен'),
    (BUYER_CONTACT_STATUS_UNSUBSCRIBED, 'Отписался'),
    (BUYER_CONTACT_STATUS_BLOCKED, 'Заблокирован'),
]

BUYER_CONTACT_SOURCE_REQUEST = 'request'
BUYER_CONTACT_SOURCE_IMPORT = 'import'
BUYER_CONTACT_SOURCE_ADMIN = 'admin'

BUYER_CONTACT_SOURCE_CHOICES = [
    (BUYER_CONTACT_SOURCE_REQUEST, 'Заявка'),
    (BUYER_CONTACT_SOURCE_IMPORT, 'Импорт'),
    (BUYER_CONTACT_SOURCE_ADMIN, 'Админ'),
]

BUYER_CITY_INTEREST_REQUEST_CITY = 'request_city'
BUYER_CITY_INTEREST_SELECTED_CITY = 'selected_city'

BUYER_CITY_INTEREST_TYPE_CHOICES = [
    (BUYER_CITY_INTEREST_REQUEST_CITY, 'Город заявки'),
    (BUYER_CITY_INTEREST_SELECTED_CITY, 'Выбранный город'),
]

CONTACT_CONSENT_CHANNEL_WHATSAPP = 'whatsapp'

CONTACT_CONSENT_CHANNEL_CHOICES = [
    (CONTACT_CONSENT_CHANNEL_WHATSAPP, 'WhatsApp'),
]

CONTACT_CONSENT_PURPOSE_SERVICE = 'service'
CONTACT_CONSENT_PURPOSE_INFORMATION = 'information'
CONTACT_CONSENT_PURPOSE_MARKETING = 'marketing'

CONTACT_CONSENT_PURPOSE_CHOICES = [
    (CONTACT_CONSENT_PURPOSE_SERVICE, 'Сервисные'),
    (CONTACT_CONSENT_PURPOSE_INFORMATION, 'Информационные'),
    (CONTACT_CONSENT_PURPOSE_MARKETING, 'Рекламные'),
]

CONTACT_CONSENT_STATUS_UNKNOWN = 'unknown'
CONTACT_CONSENT_STATUS_GRANTED = 'granted'
CONTACT_CONSENT_STATUS_REVOKED = 'revoked'

CONTACT_CONSENT_STATUS_CHOICES = [
    (CONTACT_CONSENT_STATUS_UNKNOWN, 'Неизвестно'),
    (CONTACT_CONSENT_STATUS_GRANTED, 'Дано'),
    (CONTACT_CONSENT_STATUS_REVOKED, 'Отозвано'),
]

CONTACT_CONSENT_SOURCE_REQUEST_FORM = 'request_form'
CONTACT_CONSENT_SOURCE_REGISTRATION = 'registration'
CONTACT_CONSENT_SOURCE_BUYER_PORTAL = 'buyer_portal'
CONTACT_CONSENT_SOURCE_WHATSAPP = 'whatsapp'
CONTACT_CONSENT_SOURCE_ADMIN = 'admin'
CONTACT_CONSENT_SOURCE_IMPORT = 'import'

CONTACT_CONSENT_SOURCE_CHOICES = [
    (CONTACT_CONSENT_SOURCE_REQUEST_FORM, 'Форма заявки'),
    (CONTACT_CONSENT_SOURCE_REGISTRATION, 'Регистрация'),
    (CONTACT_CONSENT_SOURCE_BUYER_PORTAL, 'Buyer portal'),
    (CONTACT_CONSENT_SOURCE_WHATSAPP, 'WhatsApp'),
    (CONTACT_CONSENT_SOURCE_ADMIN, 'Админ'),
    (CONTACT_CONSENT_SOURCE_IMPORT, 'Импорт'),
]


class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = 'Страна'
        verbose_name_plural = 'Страны'
        ordering = ['name']

    def __str__(self):
        return self.name


class Brand(models.Model):
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='brands')
    name = models.CharField(max_length=100)
    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES, default='car')

    class Meta:
        verbose_name = 'Марка'
        verbose_name_plural = 'Марки'
        ordering = ['name']
        unique_together = ('country', 'name', 'transport_type')

    def __str__(self):
        return f"{self.name} ({self.get_transport_type_display()})"


class CarModel(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name='car_models')
    name = models.CharField(max_length=100)
    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES, default='car')

    class Meta:
        verbose_name = 'Модель'
        verbose_name_plural = 'Модели'
        ordering = ['name']
        unique_together = ('brand', 'name', 'transport_type')

    def __str__(self):
        return f"{self.name} ({self.get_transport_type_display()})"


class PartCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = 'Категория запчастей'
        verbose_name_plural = 'Категории запчастей'
        ordering = ['name']

    def __str__(self):
        return self.name


class BroadcastSettings(models.Model):
    MODE_OFF = 'off'
    MODE_TEST = 'test'
    MODE_LIVE = 'live'

    MODE_CHOICES = [
        (MODE_OFF, 'OFF — рассылка выключена'),
        (MODE_TEST, 'TEST — только тестовые продавцы'),
        (MODE_LIVE, 'LIVE — боевая рассылка'),
    ]

    mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default=MODE_OFF,
        verbose_name='Режим рассылки'
    )

    wave_size = models.PositiveIntegerField(
        default=10,
        verbose_name='Размер волны',
        help_text='Сколько продавцов получает заявку в одной волне.'
    )

    wave_interval_minutes = models.PositiveIntegerField(
        default=5,
        verbose_name='Интервал между волнами, минут',
        help_text='Через сколько минут отправлять следующую волну.'
    )

    emergency_stop = models.BooleanField(
        default=False,
        verbose_name='Emergency Stop',
        help_text='Если включено, все следующие волны рассылки должны быть остановлены.'
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Broadcast Control'
        verbose_name_plural = 'Broadcast Control'

    def __str__(self):
        return f"Broadcast Control: {self.get_mode_display()}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class Request(models.Model):
    SEARCH_SCOPE_CHOICES = REQUEST_SEARCH_SCOPE_CHOICES

    transport_type = models.CharField(
        max_length=10,
        choices=TRANSPORT_CHOICES
    )

    country = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, blank=True)
    article = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    city = models.CharField(
        max_length=100,
        blank=True
    )

    search_scope = models.CharField(
        max_length=20,
        choices=REQUEST_SEARCH_SCOPE_CHOICES,
        default='city'
    )

    selected_cities = models.TextField(
        blank=True,
        default=''
    )

    phone = models.CharField(max_length=20)

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    status = models.CharField(
        max_length=20,
        default='new'
    )

    access_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        verbose_name='Токен доступа покупателя',
    )

    short_token = models.CharField(
        max_length=12,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name = 'Заявка'
        verbose_name_plural = 'Заявки'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.brand} {self.model} ({self.phone})"

    def save(self, *args, **kwargs):
        if not self.short_token:
            allowed_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
            while True:
                token = get_random_string(6, allowed_chars=allowed_chars)
                qs = Request.objects.filter(short_token=token)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                if not qs.exists():
                    self.short_token = token
                    break
        super().save(*args, **kwargs)


class BuyerPortalAccess(models.Model):
    phone_normalized = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        verbose_name='Нормализованный телефон',
    )
    access_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        verbose_name='Токен истории заявок',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Доступ покупателя к истории'
        verbose_name_plural = 'Доступы покупателей к истории'

    def __str__(self):
        return f'Покупатель {self.phone_normalized}'


class BuyerContact(models.Model):
    phone_normalized = models.CharField(
        max_length=11,
        unique=True,
        verbose_name='Нормализованный телефон',
    )
    primary_country = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Основная страна',
    )
    primary_city = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Основной город',
    )
    first_request_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Первая заявка',
    )
    last_request_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последняя заявка',
    )
    requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Количество заявок',
    )
    last_search_scope = models.CharField(
        max_length=20,
        choices=REQUEST_SEARCH_SCOPE_CHOICES,
        blank=True,
        default='',
        verbose_name='Последний режим поиска',
    )
    city_scope_requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Заявок: только город',
    )
    kazakhstan_scope_requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Заявок: весь Казахстан',
    )
    custom_scope_requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Заявок: выбранные города',
    )
    status = models.CharField(
        max_length=32,
        choices=BUYER_CONTACT_STATUS_CHOICES,
        default=BUYER_CONTACT_STATUS_ACTIVE,
        verbose_name='Статус',
    )
    source = models.CharField(
        max_length=16,
        choices=BUYER_CONTACT_SOURCE_CHOICES,
        default=BUYER_CONTACT_SOURCE_REQUEST,
        verbose_name='Источник',
    )
    portal_access = models.OneToOneField(
        'BuyerPortalAccess',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='buyer_contact',
        verbose_name='Доступ buyer portal',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Контакт покупателя'
        verbose_name_plural = 'Контакты покупателей'
        ordering = ('-last_request_at', '-id')

    def __str__(self) -> str:
        return mask_phone(self.phone_normalized)


class BuyerVehicle(models.Model):
    buyer = models.ForeignKey(
        BuyerContact,
        on_delete=models.CASCADE,
        related_name='vehicles',
        verbose_name='Покупатель',
    )
    transport_type = models.CharField(
        max_length=10,
        choices=TRANSPORT_CHOICES,
        verbose_name='Тип транспорта',
    )
    brand = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Марка',
    )
    model = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Модель',
    )
    brand_normalized = models.CharField(
        max_length=100,
        verbose_name='Марка (нормализованная)',
    )
    model_normalized = models.CharField(
        max_length=100,
        verbose_name='Модель (нормализованная)',
    )
    first_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Первое появление',
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последнее появление',
    )
    requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Количество заявок',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Автомобиль покупателя'
        verbose_name_plural = 'Автомобили покупателей'
        ordering = ('-last_seen_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'buyer',
                    'transport_type',
                    'brand_normalized',
                    'model_normalized',
                ],
                name='unique_buyer_vehicle',
            ),
        ]
        indexes = [
            models.Index(
                fields=['brand_normalized', 'model_normalized'],
                name='buyer_vehicle_brand_model_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        self.brand = str(self.brand or '').strip()
        self.model = str(self.model or '').strip()
        self.brand_normalized = normalize_buyer_text(self.brand)
        self.model_normalized = normalize_buyer_text(self.model)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.brand} {self.model}'.strip() or f'Авто #{self.pk}'


class BuyerCategoryInterest(models.Model):
    buyer = models.ForeignKey(
        BuyerContact,
        on_delete=models.CASCADE,
        related_name='category_interests',
        verbose_name='Покупатель',
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Категория',
    )
    category_normalized = models.CharField(
        max_length=100,
        verbose_name='Категория (нормализованная)',
    )
    first_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Первое появление',
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последнее появление',
    )
    requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Количество заявок',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Интерес к категории'
        verbose_name_plural = 'Интересы к категориям'
        ordering = ('-last_seen_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=['buyer', 'category_normalized'],
                name='unique_buyer_category_interest',
            ),
        ]
        indexes = [
            models.Index(
                fields=['category_normalized'],
                name='buyer_category_norm_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        self.category = str(self.category or '').strip()
        self.category_normalized = normalize_buyer_text(self.category)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.category or f'Категория #{self.pk}'


class BuyerCityInterest(models.Model):
    buyer = models.ForeignKey(
        BuyerContact,
        on_delete=models.CASCADE,
        related_name='city_interests',
        verbose_name='Покупатель',
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Город',
    )
    city_normalized = models.CharField(
        max_length=100,
        verbose_name='Город (нормализованный)',
    )
    interest_type = models.CharField(
        max_length=20,
        choices=BUYER_CITY_INTEREST_TYPE_CHOICES,
        verbose_name='Тип интереса',
    )
    first_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Первое появление',
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последнее появление',
    )
    requests_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Количество заявок',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Интерес к городу'
        verbose_name_plural = 'Интересы к городам'
        ordering = ('-last_seen_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=['buyer', 'city_normalized', 'interest_type'],
                name='unique_buyer_city_interest',
            ),
        ]
        indexes = [
            models.Index(
                fields=['city_normalized', 'interest_type'],
                name='buyer_city_norm_type_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        self.city = str(self.city or '').strip()
        self.city_normalized = normalize_buyer_text(self.city)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.city or f'Город #{self.pk}'


class ContactConsent(models.Model):
    buyer = models.ForeignKey(
        BuyerContact,
        on_delete=models.CASCADE,
        related_name='consents',
        verbose_name='Покупатель',
    )
    channel = models.CharField(
        max_length=16,
        choices=CONTACT_CONSENT_CHANNEL_CHOICES,
        verbose_name='Канал',
    )
    purpose = models.CharField(
        max_length=16,
        choices=CONTACT_CONSENT_PURPOSE_CHOICES,
        verbose_name='Цель',
    )
    status = models.CharField(
        max_length=16,
        choices=CONTACT_CONSENT_STATUS_CHOICES,
        default=CONTACT_CONSENT_STATUS_UNKNOWN,
        verbose_name='Статус',
    )
    source = models.CharField(
        max_length=20,
        choices=CONTACT_CONSENT_SOURCE_CHOICES,
        blank=True,
        default='',
        verbose_name='Источник',
    )
    consent_text_version = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name='Версия текста согласия',
    )
    evidence_reference = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Ссылка на доказательство',
    )
    consented_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата согласия',
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата отзыва',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')

    class Meta:
        verbose_name = 'Согласие на контакт'
        verbose_name_plural = 'Согласия на контакт'
        ordering = ('-updated_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=['buyer', 'channel', 'purpose'],
                name='unique_buyer_consent_purpose',
            ),
        ]

    def clean(self):
        errors = {}
        if self.status == CONTACT_CONSENT_STATUS_GRANTED and not self.consented_at:
            errors['consented_at'] = (
                'Для статуса «Дано» необходимо указать дату согласия.'
            )
        if self.status == CONTACT_CONSENT_STATUS_REVOKED and not self.revoked_at:
            errors['revoked_at'] = (
                'Для статуса «Отозвано» необходимо указать дату отзыва.'
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.get_channel_display()} / {self.get_purpose_display()}'


class RequestPhoto(models.Model):
    request = models.ForeignKey(
        Request,
        on_delete=models.CASCADE,
        related_name='photos',
        verbose_name='Заявка',
    )
    image = models.ImageField(
        upload_to='request_photos/',
        verbose_name='Фото',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Загружено',
    )

    class Meta:
        verbose_name = 'Фото заявки'
        verbose_name_plural = 'Фото заявок'
        ordering = ['created_at']

    def __str__(self):
        return f'Фото #{self.pk} → заявка #{self.request_id}'


class Seller(models.Model):
    name = models.CharField(max_length=255)
    whatsapp = models.CharField(max_length=20)
    phone2 = models.CharField(max_length=20, blank=True, verbose_name='Доп. телефон')

    password_hash = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Хэш пароля'
    )

    must_change_password = models.BooleanField(
        default=True,
        verbose_name='Требует смены пароля'
    )

    seller_type = models.CharField(
        max_length=20,
        choices=SELLER_TYPE_CHOICES,
        default='seller',
        verbose_name='Тип продавца'
    )

    market_location = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Локация / бутик'
    )

    notes = models.TextField(
        blank=True,
        verbose_name='Примечания'
    )

    is_active = models.BooleanField(default=True)
    is_paused = models.BooleanField(default=False)

    receive_requests = models.BooleanField(
        default=False,
        verbose_name='Получает заявки',
        help_text='Участвует в боевой LIVE-рассылке заявок.'
    )

    is_test_seller = models.BooleanField(
        default=False,
        verbose_name='Тестовый продавец',
        help_text='Используется только в TEST-режиме рассылки.'
    )

    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES)
    city = models.CharField(max_length=100, blank=True)

    dispatch_priority = models.PositiveIntegerField(
        default=1000,
        verbose_name='Приоритет рассылки',
        help_text='Чем меньше число, тем раньше продавец получает заявку в волнах рассылки.'
    )

    category = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)

    country_fk = models.ForeignKey(
        Country,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sellers',
        verbose_name='Страна'
    )

    brand_fk = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sellers',
        verbose_name='Марка'
    )

    model_fk = models.ForeignKey(
        CarModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sellers',
        verbose_name='Модель'
    )

    selected_categories = models.ManyToManyField(
        PartCategory,
        blank=True,
        related_name='sellers',
        verbose_name='Выбранные категории'
    )

    selected_countries = models.ManyToManyField(
        Country,
        blank=True,
        related_name='multi_sellers',
        verbose_name='Выбранные страны'
    )

    selected_brands = models.ManyToManyField(
        Brand,
        blank=True,
        related_name='multi_sellers',
        verbose_name='Выбранные марки'
    )

    selected_models = models.ManyToManyField(
        CarModel,
        blank=True,
        related_name='multi_sellers',
        verbose_name='Выбранные модели'
    )

    all_countries = models.BooleanField(default=False, verbose_name='Все страны')
    all_brands = models.BooleanField(default=False, verbose_name='Все марки')
    all_models = models.BooleanField(default=False, verbose_name='Все модели')
    all_categories = models.BooleanField(default=False, verbose_name='Все категории')

    class Meta:
        verbose_name = 'Продавец'
        verbose_name_plural = 'Продавцы'
        ordering = ['dispatch_priority', 'id', 'name']

    def __str__(self):
        return self.name


class Match(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name='matches')
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='matches')

    status = models.CharField(max_length=20, default='prepared')

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Отправка заявки'
        verbose_name_plural = 'Отправки заявок'
        ordering = ['-created_at']
        unique_together = ('request', 'seller')

    def __str__(self):
        return f"{self.request} → {self.seller}"


class RequestDispatch(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENT = 'sent'
    STATUS_PAUSED = 'paused'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_QUEUED, 'В очереди'),
        (STATUS_SENT, 'Отправлено'),
        (STATUS_PAUSED, 'Остановлено'),
        (STATUS_FAILED, 'Ошибка'),
    ]

    request = models.ForeignKey(
        Request,
        on_delete=models.CASCADE,
        related_name='dispatches',
        verbose_name='Заявка'
    )

    seller = models.ForeignKey(
        Seller,
        on_delete=models.CASCADE,
        related_name='dispatches',
        verbose_name='Продавец'
    )

    wave_number = models.PositiveIntegerField(verbose_name='Номер волны')
    position_number = models.PositiveIntegerField(verbose_name='Позиция в очереди')

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        verbose_name='Статус'
    )

    scheduled_at = models.DateTimeField(verbose_name='Запланировано на')
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Отправлено в')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Очередь рассылки заявки'
        verbose_name_plural = 'Очередь рассылки заявок'
        ordering = ['request', 'position_number']
        unique_together = ('request', 'seller')

    def __str__(self):
        return f"{self.request} → {self.seller} / волна {self.wave_number}"

class WhatsAppMessageLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    request_id = models.IntegerField(null=True, blank=True)
    seller_name = models.CharField(max_length=255)
    phone_clean = models.CharField(max_length=20)

    is_success = models.BooleanField(default=False)
    status_text = models.CharField(max_length=50, blank=True)
    message_id = models.CharField(max_length=255, blank=True)

    error_text = models.TextField(blank=True)

    def __str__(self):
        return f"{self.seller_name} - {self.phone_clean}"


class Feedback(models.Model):
    name = models.CharField(max_length=120, verbose_name='Имя')
    phone = models.CharField(max_length=30, verbose_name='Телефон')
    message = models.TextField(verbose_name='Сообщение')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        verbose_name = 'Обратная связь'
        verbose_name_plural = 'Обратная связь'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.phone})'


class InstagramPublication(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_APPROVED = 'approved'
    STATUS_QUEUED = 'queued'
    STATUS_PUBLISHING = 'publishing'
    STATUS_PUBLISHED = 'published'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Черновик'),
        (STATUS_APPROVED, 'Одобрено'),
        (STATUS_QUEUED, 'В очереди'),
        (STATUS_PUBLISHING, 'Публикуется'),
        (STATUS_PUBLISHED, 'Опубликовано'),
        (STATUS_FAILED, 'Ошибка'),
        (STATUS_CANCELLED, 'Отменено'),
    ]

    request = models.OneToOneField(
        Request,
        on_delete=models.CASCADE,
        related_name='instagram_publication',
        verbose_name='Заявка',
    )
    image = models.ImageField(
        upload_to='instagram_stories/',
        verbose_name='Карточка Story',
    )
    caption = models.TextField(
        blank=True,
        default='',
        verbose_name='Безопасный текст',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name='Статус',
    )
    instagram_container_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Instagram container ID',
    )
    instagram_media_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Instagram media ID',
    )
    error_message = models.TextField(
        blank=True,
        default='',
        verbose_name='Текст ошибки',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано',
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Опубликовано',
    )
    publishing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Начало публикации',
    )
    retry_count = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Число попыток публикации',
    )
    last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последняя попытка',
    )

    class Meta:
        verbose_name = 'Публикация Instagram'
        verbose_name_plural = 'Публикации Instagram'
        ordering = ['-created_at']

    def __str__(self):
        return f'Instagram #{self.pk} — заявка #{self.request_id} ({self.get_status_display()})'


SELLER_LEAD_STATUS_CHOICES = [
    ('new', 'Найден'),
    ('needs_review', 'Требует проверки'),
    ('verified', 'Проверен'),
    ('no_whatsapp', 'Нет WhatsApp'),
    ('contacted', 'Написали'),
    ('replied', 'Ответил'),
    ('interested', 'Заинтересован'),
    ('registered', 'Зарегистрирован'),
    ('rejected', 'Отказался'),
    ('duplicate', 'Дубликат'),
    ('not_seller', 'Не продавец'),
]

SELLER_LEAD_SOURCE_TYPE_CHOICES = [
    ('instagram_search', 'Поиск в Instagram'),
    ('instagram_hashtag', 'Хэштег Instagram'),
    ('instagram_profile', 'Профиль Instagram'),
    ('instagram_post', 'Публикация Instagram'),
    ('web_search', 'Веб-поиск'),
    ('manual', 'Вручную'),
    ('other', 'Другое'),
]

WHATSAPP_CONFIDENCE_CHOICES = [
    ('high', 'Высокая'),
    ('medium', 'Средняя'),
    ('low', 'Низкая'),
]


def normalize_seller_lead_instagram_username(value: str | None) -> str:
    username = str(value or '').strip()
    if username.startswith('@'):
        username = username[1:].strip()
    return username


def normalize_seller_lead_whatsapp(value: str | None) -> str:
    digits = ''.join(char for char in str(value or '') if char.isdigit())
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    return digits


class SellerLead(models.Model):
    STATUS_NEW = 'new'
    STATUS_NEEDS_REVIEW = 'needs_review'
    STATUS_VERIFIED = 'verified'
    STATUS_NO_WHATSAPP = 'no_whatsapp'
    STATUS_CONTACTED = 'contacted'
    STATUS_REPLIED = 'replied'
    STATUS_INTERESTED = 'interested'
    STATUS_REGISTERED = 'registered'
    STATUS_REJECTED = 'rejected'
    STATUS_DUPLICATE = 'duplicate'
    STATUS_NOT_SELLER = 'not_seller'

    name = models.CharField(max_length=255, verbose_name='Название')
    instagram_username = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Instagram username',
    )
    instagram_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Ссылка на Instagram',
    )
    whatsapp = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name='WhatsApp',
    )
    whatsapp_source_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Источник WhatsApp',
    )
    whatsapp_source_text = models.TextField(
        blank=True,
        default='',
        verbose_name='Фрагмент текста с WhatsApp',
    )
    whatsapp_confidence = models.CharField(
        max_length=10,
        choices=WHATSAPP_CONFIDENCE_CHOICES,
        blank=True,
        default='',
        verbose_name='Уверенность WhatsApp',
    )
    whatsapp_found_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата обнаружения WhatsApp',
    )
    city = models.CharField(max_length=100, blank=True, default='', verbose_name='Город')
    category = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Категория запчастей',
    )
    car_brands = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Марки автомобилей',
    )
    profile_description = models.TextField(
        blank=True,
        default='',
        verbose_name='Описание профиля Instagram',
    )
    website_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Сайт / Taplink / Linktree',
    )
    source_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Источник контакта',
    )
    source_type = models.CharField(
        max_length=32,
        choices=SELLER_LEAD_SOURCE_TYPE_CHOICES,
        default='manual',
        verbose_name='Тип источника',
    )
    status = models.CharField(
        max_length=20,
        choices=SELLER_LEAD_STATUS_CHOICES,
        default=STATUS_NEEDS_REVIEW,
        verbose_name='Статус',
    )
    notes = models.TextField(blank=True, default='', verbose_name='Комментарий оператора')
    collected_at = models.DateTimeField(
        default=timezone.now,
        verbose_name='Дата обнаружения',
    )
    checked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата проверки',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Потенциальный продавец'
        verbose_name_plural = 'Потенциальные продавцы'
        ordering = ['-collected_at', '-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['city']),
            models.Index(fields=['category']),
            models.Index(fields=['instagram_username']),
            models.Index(fields=['whatsapp']),
            models.Index(fields=['collected_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['instagram_username'],
                condition=models.Q(instagram_username__gt=''),
                name='unique_sellerlead_instagram_username',
            ),
            models.UniqueConstraint(
                fields=['whatsapp'],
                condition=models.Q(whatsapp__gt=''),
                name='unique_sellerlead_whatsapp',
            ),
        ]

    def __str__(self):
        if self.instagram_username:
            return f'{self.name} — @{self.instagram_username}'
        if self.whatsapp:
            return f'{self.name} — {self.whatsapp}'
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        self.instagram_username = normalize_seller_lead_instagram_username(self.instagram_username)
        self.whatsapp = normalize_seller_lead_whatsapp(self.whatsapp)

        duplicate_filter = SellerLead.objects.exclude(pk=self.pk)
        if self.instagram_username and duplicate_filter.filter(
            instagram_username=self.instagram_username,
        ).exists():
            raise ValidationError({
                'instagram_username': 'Потенциальный продавец с таким Instagram username уже существует.',
            })
        if self.whatsapp and duplicate_filter.filter(whatsapp=self.whatsapp).exists():
            raise ValidationError({
                'whatsapp': 'Потенциальный продавец с таким WhatsApp уже существует.',
            })

    def save(self, *args, **kwargs):
        from django.utils import timezone

        self.instagram_username = normalize_seller_lead_instagram_username(self.instagram_username)
        self.whatsapp = normalize_seller_lead_whatsapp(self.whatsapp)
        if self.status == self.STATUS_VERIFIED and not self.checked_at:
            self.checked_at = timezone.now()
        super().save(*args, **kwargs)

    def get_instagram_profile_url(self) -> str:
        if self.instagram_url:
            return self.instagram_url
        if self.instagram_username:
            return f'https://www.instagram.com/{self.instagram_username}/'
        return ''

    def get_whatsapp_url(self) -> str:
        if self.whatsapp:
            return f'https://wa.me/{self.whatsapp}'
        return ''


CONTACT_CANDIDATE_TYPE_CHOICES = [
    ('whatsapp', 'WhatsApp'),
    ('phone', 'Телефон'),
]

CONTACT_CANDIDATE_ROLE_CHOICES = [
    ('unknown', 'Не указано'),
    ('shop', 'Магазин'),
    ('service', 'Сервис'),
    ('sales', 'Продажи'),
    ('warehouse', 'Склад'),
    ('administration', 'Администрация'),
]

CONTACT_CANDIDATE_SOURCE_TYPE_CHOICES = [
    ('instagram_snippet', 'Сниппет Instagram'),
    ('wa_me', 'wa.me'),
    ('website', 'Сайт'),
    ('directory', 'Справочник'),
    ('facebook', 'Facebook'),
    ('manual', 'Вручную'),
    ('other', 'Другое'),
]

CONTACT_CANDIDATE_STATUS_CHOICES = [
    ('pending', 'Ожидает проверки'),
    ('approved', 'Подтверждён'),
    ('rejected', 'Отклонён'),
    ('conflict', 'Конфликт'),
]

CONTACT_CANDIDATE_SOURCE_TEXT_LIMIT = 400


def normalize_contact_candidate_value(value: str | None) -> str:
    return normalize_seller_lead_whatsapp(value)


class SellerLeadContactCandidate(models.Model):
    CONTACT_TYPE_WHATSAPP = 'whatsapp'
    CONTACT_TYPE_PHONE = 'phone'

    ROLE_UNKNOWN = 'unknown'
    ROLE_SHOP = 'shop'
    ROLE_SERVICE = 'service'
    ROLE_SALES = 'sales'
    ROLE_WAREHOUSE = 'warehouse'
    ROLE_ADMINISTRATION = 'administration'

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CONFLICT = 'conflict'

    seller_lead = models.ForeignKey(
        SellerLead,
        on_delete=models.CASCADE,
        related_name='contact_candidates',
        verbose_name='Потенциальный продавец',
    )
    contact_type = models.CharField(
        max_length=16,
        choices=CONTACT_CANDIDATE_TYPE_CHOICES,
        default=CONTACT_TYPE_WHATSAPP,
        verbose_name='Тип контакта',
    )
    value = models.CharField(max_length=20, verbose_name='Значение')
    role = models.CharField(
        max_length=20,
        choices=CONTACT_CANDIDATE_ROLE_CHOICES,
        default=ROLE_UNKNOWN,
        verbose_name='Назначение',
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Описание',
    )
    confidence = models.CharField(
        max_length=10,
        choices=WHATSAPP_CONFIDENCE_CHOICES,
        verbose_name='Уверенность',
    )
    source_url = models.URLField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='URL источника',
    )
    source_text = models.TextField(
        blank=True,
        default='',
        verbose_name='Фрагмент доказательства',
    )
    source_type = models.CharField(
        max_length=32,
        choices=CONTACT_CANDIDATE_SOURCE_TYPE_CHOICES,
        default='other',
        verbose_name='Тип источника',
    )
    status = models.CharField(
        max_length=16,
        choices=CONTACT_CANDIDATE_STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='Статус',
    )
    is_primary = models.BooleanField(default=False, verbose_name='Основной контакт')
    found_at = models.DateTimeField(default=timezone.now, verbose_name='Дата обнаружения')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата проверки')
    notes = models.TextField(blank=True, default='', verbose_name='Комментарий')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Кандидат контакта'
        verbose_name_plural = 'Кандидаты контактов'
        ordering = ['-found_at', '-created_at']
        indexes = [
            models.Index(fields=['seller_lead', 'status']),
            models.Index(fields=['value']),
            models.Index(fields=['confidence']),
            models.Index(fields=['is_primary']),
            models.Index(fields=['found_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['seller_lead', 'contact_type', 'value'],
                name='unique_sellerlead_contact_candidate_value',
            ),
            models.UniqueConstraint(
                fields=['seller_lead'],
                condition=models.Q(is_primary=True),
                name='unique_sellerlead_primary_contact_candidate',
            ),
        ]

    def __str__(self) -> str:
        username = self.seller_lead.instagram_username or self.seller_lead.name
        role_label = self.get_role_display()
        return f'@{username} — {self.value} — {role_label}'

    def clean(self):
        from django.core.exceptions import ValidationError

        normalized = normalize_contact_candidate_value(self.value)
        if not normalized:
            raise ValidationError({'value': 'Номер контакта не может быть пустым.'})
        if self.contact_type == self.CONTACT_TYPE_WHATSAPP:
            if len(normalized) != 11 or not normalized.startswith('7'):
                raise ValidationError({'value': 'WhatsApp должен содержать 11 цифр и начинаться с 7.'})
            if len(set(normalized)) == 1:
                raise ValidationError({'value': 'Недопустимый номер WhatsApp.'})
        self.value = normalized
        if self.source_text:
            self.source_text = self.source_text[:CONTACT_CANDIDATE_SOURCE_TEXT_LIMIT]
        if self.source_url:
            self.source_url = self.source_url[:500]

    def save(self, *args, **kwargs):
        normalized = normalize_contact_candidate_value(self.value)
        if normalized:
            self.value = normalized
        if self.source_text:
            self.source_text = self.source_text[:CONTACT_CANDIDATE_SOURCE_TEXT_LIMIT]
        if self.source_url:
            self.source_url = self.source_url[:500]
        super().save(*args, **kwargs)

    def get_whatsapp_url(self) -> str:
        if self.contact_type == self.CONTACT_TYPE_WHATSAPP and self.value:
            return f'https://wa.me/{self.value}'
        return ''

    def approve_as_primary(self) -> None:
        from django.db import transaction

        if self.status == self.STATUS_REJECTED:
            raise ValueError('Отклонённый кандидат не может стать основным контактом.')
        if not self.value:
            raise ValueError('Пустой номер не может стать основным контактом.')

        with transaction.atomic():
            now = timezone.now()
            SellerLeadContactCandidate.objects.filter(
                seller_lead=self.seller_lead,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)

            self.status = self.STATUS_APPROVED
            self.is_primary = True
            self.reviewed_at = now
            self.save(
                update_fields=[
                    'status',
                    'is_primary',
                    'reviewed_at',
                    'updated_at',
                ],
            )

            lead = self.seller_lead
            lead.whatsapp = self.value
            lead.whatsapp_confidence = self.confidence
            lead.whatsapp_source_url = self.source_url
            lead.whatsapp_source_text = self.source_text
            lead.whatsapp_found_at = self.found_at
            lead.save(
                update_fields=[
                    'whatsapp',
                    'whatsapp_confidence',
                    'whatsapp_source_url',
                    'whatsapp_source_text',
                    'whatsapp_found_at',
                    'updated_at',
                ],
            )


class SellerLeadPipelineRun(models.Model):
    TRIGGER_MANUAL = 'manual'
    TRIGGER_CRON = 'cron'
    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL, 'Ручной'),
        (TRIGGER_CRON, 'Cron'),
    ]

    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_PARTIAL = 'partial'
    STATUS_FAILED = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Выполняется'),
        (STATUS_SUCCESS, 'Успешно'),
        (STATUS_PARTIAL, 'Частично'),
        (STATUS_FAILED, 'Ошибка'),
        (STATUS_SKIPPED, 'Пропущен'),
    ]

    run_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, verbose_name='UUID запуска')
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_MANUAL, verbose_name='Источник')
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        verbose_name='Статус',
    )
    is_dry_run = models.BooleanField(default=False, verbose_name='Dry-run')
    city = models.CharField(max_length=100, verbose_name='Город')
    category = models.CharField(max_length=100, verbose_name='Категория')
    search_limit = models.PositiveSmallIntegerField(verbose_name='Лимит поиска')
    lead_limit = models.PositiveSmallIntegerField(verbose_name='Лимит лидов')
    max_queries_per_lead = models.PositiveSmallIntegerField(verbose_name='Лимит запросов на лид')
    search_term = models.CharField(max_length=200, blank=True, default='', verbose_name='Поисковый термин')
    rotation_enabled = models.BooleanField(default=False, verbose_name='Ротация включена')
    rotation_slug = models.CharField(max_length=100, blank=True, default='', verbose_name='Профиль ротации')
    rotation_index = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Индекс ротации',
    )
    skip_discovery = models.BooleanField(default=False, verbose_name='Пропуск discovery')
    skip_enrichment = models.BooleanField(default=False, verbose_name='Пропуск enrichment')
    cooldown_minutes = models.PositiveIntegerField(default=0, verbose_name='Cooldown (мин)')
    force_run = models.BooleanField(default=False, verbose_name='Force run')
    started_at = models.DateTimeField(default=timezone.now, verbose_name='Начало')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='Окончание')
    discovery_stats = models.JSONField(default=dict, blank=True, verbose_name='Статистика discovery')
    enrichment_stats = models.JSONField(default=dict, blank=True, verbose_name='Статистика enrichment')
    created_lead_ids = models.JSONField(default=list, blank=True, verbose_name='Созданные лиды (ID)')
    error_message = models.TextField(blank=True, default='', verbose_name='Сообщение об ошибке')
    skip_reason = models.TextField(blank=True, default='', verbose_name='Причина пропуска')

    class Meta:
        verbose_name = 'Запуск SellerLead pipeline'
        verbose_name_plural = 'Запуски SellerLead pipeline'
        ordering = ['-started_at']

    def __str__(self) -> str:
        return f'{self.run_uuid} ({self.status})'