from django.db import models


TRANSPORT_CHOICES = [
    ('car', 'Легковые'),
    ('truck', 'Грузовые'),
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
    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES)
    country = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, blank=True)
    article = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20)

    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='new')

    class Meta:
        verbose_name = 'Заявка'
        verbose_name_plural = 'Заявки'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.brand} {self.model} ({self.phone})"


class Seller(models.Model):
    name = models.CharField(max_length=255)
    whatsapp = models.CharField(max_length=20)

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

    # Старые поля оставляем для совместимости
    category = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)

    # Старые одиночные FK оставляем для совместимости
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

    # Новый множественный выбор
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

    # Режимы "все"
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