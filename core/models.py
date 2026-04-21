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


class Request(models.Model):
    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES)
    country = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, blank=True)
    article = models.CharField(max_length=100, blank=True)
    description = models.TextField()
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

    transport_type = models.CharField(max_length=10, choices=TRANSPORT_CHOICES)
    city = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=100, blank=True)

    # Старые поля оставляем, чтобы ничего не сломать
    brand = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)

    # Новые безопасные поля для перехода на справочники
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

    class Meta:
        verbose_name = 'Продавец'
        verbose_name_plural = 'Продавцы'
        ordering = ['name']

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

    def __str__(self):
        return f"{self.request} → {self.seller}"