from django.db import models

# ================================
# Настройки рассылки исполнителям
# ================================

class ServiceBroadcastSettings(models.Model):
    MODE_OFF = 'off'
    MODE_TEST = 'test'
    MODE_LIVE = 'live'

    MODE_CHOICES = [
        (MODE_OFF, 'OFF — рассылка выключена'),
        (MODE_TEST, 'TEST — только тестовые исполнители'),
        (MODE_LIVE, 'LIVE — боевая рассылка'),
    ]

    mode = models.CharField(
        max_length=10,
        choices=MODE_CHOICES,
        default=MODE_OFF,
        verbose_name='Режим рассылки исполнителям'
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено'
    )

    class Meta:
        verbose_name = 'Управление рассылкой'
        verbose_name_plural = 'Управление рассылкой'

    def __str__(self):
        return f"Service Broadcast Control: {self.get_mode_display()}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


# ================================
# Услуги (СТО / детейлинг)
# ================================

class Service(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = 'Услуга'
        verbose_name_plural = 'Услуги'

    def __str__(self):
        return self.name


# ================================
# Исполнители (СТО / детейлинг)
# ================================

class ServiceSeller(models.Model):
    SELLER_TYPES = [
        ('sto', 'СТО'),
        ('detailing', 'Детейлинг'),
    ]

    name = models.CharField(max_length=255)
    whatsapp = models.CharField(max_length=20, unique=True)
    password = models.CharField(max_length=128)

    city = models.CharField(max_length=100)
    district = models.CharField(max_length=100, blank=True)

    address = models.CharField(
        max_length=255,
        blank=True
    )

    logo = models.ImageField(
        upload_to='service_sellers/logos/',
        blank=True,
        null=True
    )

    description = models.TextField(
        blank=True
    )

    instagram = models.URLField(
        blank=True
    )

    website = models.URLField(
        blank=True
    )

    working_hours = models.CharField(
        max_length=255,
        blank=True
    )

    map_link = models.URLField(
        blank=True
    )

    seller_type = models.CharField(max_length=20, choices=SELLER_TYPES)

    services = models.ManyToManyField(Service, blank=True)

    is_active = models.BooleanField(default=True)

    receive_requests = models.BooleanField(
        default=True,
        verbose_name='Получает заявки',
        help_text='Участвует в LIVE-рассылке заявок исполнителям.'
    )

    is_test_seller = models.BooleanField(
        default=False,
        verbose_name='Тестовый исполнитель',
        help_text='Используется только в TEST-режиме рассылки.'
    )

    is_paused = models.BooleanField(
        default=False,
        verbose_name='Пауза',
        help_text='Если включено, исполнитель временно не получает заявки.'
    )

    dispatch_priority = models.PositiveIntegerField(
        default=1000,
        verbose_name='Приоритет рассылки',
        help_text='Чем меньше число, тем раньше исполнитель получает заявку.'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Исполнитель'
        verbose_name_plural = 'Исполнители'

    def __str__(self):
        return f"{self.name} ({self.seller_type})"


# ================================
# Заявки клиентов
# ================================

class ServiceRequest(models.Model):
    REQUEST_TYPES = [
        ('sto', 'СТО'),
        ('detailing', 'Детейлинг'),
    ]

    service_type = models.CharField(max_length=20, choices=REQUEST_TYPES)

    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)

    services = models.ManyToManyField(Service)

    city = models.CharField(max_length=100)

    district = models.CharField(max_length=100, blank=True)

    phone = models.CharField(max_length=20)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Заявка клиента'
        verbose_name_plural = 'Заявки клиентов'

    def __str__(self):
        return f"{self.service_type} | {self.city}"


# ================================
# Матчинг (кому отправили заявку)
# ================================

class ServiceMatch(models.Model):
    STATUS = [
        ('new', 'Новая'),
        ('sent', 'Отправлена'),
        ('viewed', 'Просмотрена'),
        ('in_work', 'В работе'),
        ('done', 'Завершена'),
    ]

    request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE
    )

    seller = models.ForeignKey(
        ServiceSeller,
        on_delete=models.CASCADE
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='new'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        verbose_name = 'Назначение исполнителю'
        verbose_name_plural = 'Назначения исполнителям'


# ================================
# WhatsApp Message Logs исполнителей
# ================================

class ServiceWhatsAppMessageLog(models.Model):

    STATUS = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    MESSAGE_TYPES = [
        ('seller_request', 'Заявка исполнителю'),
        ('buyer_notice', 'Уведомление клиенту'),
        ('manual', 'Ручная отправка'),
    ]

    seller = models.ForeignKey(
        ServiceSeller,
        on_delete=models.CASCADE,
        related_name='wa_logs'
    )

    request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wa_logs'
    )

    phone = models.CharField(
        max_length=30
    )

    message_type = models.CharField(
        max_length=30,
        choices=MESSAGE_TYPES,
        default='seller_request'
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='pending'
    )

    meta_message_id = models.CharField(
        max_length=255,
        blank=True
    )

    error_text = models.TextField(
        blank=True
    )

    response_json = models.TextField(
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'WhatsApp лог исполнителя'
        verbose_name_plural = 'WhatsApp логи исполнителей'

    def __str__(self):

        return (
            f"{self.phone} | "
            f"{self.get_status_display()} | "
            f"{self.created_at:%d.%m.%Y %H:%M}"
        )