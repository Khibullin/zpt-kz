from django.db import models


# ================================
# Услуги (СТО / детейлинг)
# ================================

class Service(models.Model):
    name = models.CharField(max_length=100)

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

    address = models.CharField(max_length=255, blank=True)

    # ссылка на 2GIS
    map_link = models.URLField(blank=True)

    seller_type = models.CharField(max_length=20, choices=SELLER_TYPES)

    services = models.ManyToManyField(Service, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

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

    # желаемый район клиента
    district = models.CharField(max_length=100, blank=True)

    phone = models.CharField(max_length=20)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

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

    request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE)
    seller = models.ForeignKey(ServiceSeller, on_delete=models.CASCADE)

    status = models.CharField(max_length=20, choices=STATUS, default='new')
    created_at = models.DateTimeField(auto_now_add=True)