import uuid

from django.db import models
from django.utils.crypto import get_random_string


TRANSPORT_CHOICES = [
    ('car', 'Легковые'),
    ('truck', 'Грузовые'),
]


SELLER_TYPE_CHOICES = [
    ('seller', 'Продавец запчастей'),
    ('service', 'Сервис / СТО'),
    ('both', 'Продавец + сервис'),
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
    SEARCH_SCOPE_CHOICES = [
        ('city', 'Только мой город'),
        ('kazakhstan', 'Весь Казахстан'),
        ('custom', 'Выбрать города'),
    ]

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
        choices=SEARCH_SCOPE_CHOICES,
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