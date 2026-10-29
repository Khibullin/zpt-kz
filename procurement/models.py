from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Membership(models.Model):
    class Role(models.TextChoices):
        USER = 'user', 'Пользователь'
        SALES = 'sales', 'Менеджер-продажник'
        ADMIN = 'admin', 'Администратор'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.USER)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.user}: {self.get_role_display()}'


class Supplier(models.Model):
    name = models.CharField(max_length=160, unique=True)
    website = models.URLField(blank=True)

    def __str__(self):
        return self.name


class ManagerInvitation(models.Model):
    email = models.EmailField(unique=True)
    token_digest = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='procurement_invitations')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='procurement_invitation')


class Offer(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    brand = models.CharField(max_length=100)
    article = models.CharField(max_length=120)
    name = models.CharField(max_length=500)
    category = models.CharField(max_length=200, blank=True)
    applicability = models.TextField(blank=True)
    description = models.TextField(blank=True)
    photo_url = models.URLField(blank=True)
    supplier_url = models.URLField(blank=True)
    characteristics = models.JSONField(default=dict, blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    stock = models.CharField(max_length=100, blank=True)
    min_order = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    lead_days = models.PositiveIntegerField(null=True, blank=True)
    kaspi_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    kaspi_url = models.URLField(blank=True)
    match_verified = models.BooleanField(default=False)
    review_count = models.PositiveIntegerField(null=True, blank=True)
    recent_reviews = models.PositiveIntegerField(null=True, blank=True)
    sales_30 = models.PositiveIntegerField(null=True, blank=True)
    last_review = models.DateField(null=True, blank=True)
    evidence_date = models.DateField(null=True, blank=True)
    evidence_source = models.CharField(max_length=250, blank=True)
    checked_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['supplier', 'brand', 'article'], name='procurement_supplier_article')]
        ordering = ['brand', 'article', 'pk']


class RetailListing(models.Model):
    offer = models.OneToOneField(Offer, on_delete=models.PROTECT, related_name='retail_listing')
    product = models.OneToOneField('catalog.Product', on_delete=models.PROTECT, related_name='omega_listing')
    pack_size = models.PositiveIntegerField(default=1)
    source_content = models.JSONField(default=dict)
    enabled = models.BooleanField(default=True)
    hold_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Preferences(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    commission = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
    expenses = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    min_profit = models.DecimalField(max_digits=12, decimal_places=2, default=1000, validators=[MinValueValidator(0)])
    min_markup = models.DecimalField(max_digits=7, decimal_places=2, default=30, validators=[MinValueValidator(0)])


class Selection(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'offer'], name='procurement_user_selection')]


class Draft(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        READY = 'ready', 'Готов к проверке'
    offer = models.OneToOneField(Offer, on_delete=models.CASCADE)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    photo_url = models.URLField(blank=True)
    category = models.CharField(max_length=200, blank=True)
    characteristics = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)


class Activity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'offer'], name='procurement_user_favorite')]


class VehicleFitment(models.Model):
    """One sourced make/model pair; never infer fitment from the part brand."""
    offer = models.ForeignKey(Offer, related_name='fitments', on_delete=models.CASCADE)
    make = models.CharField('Марка автомобиля', max_length=100)
    model = models.CharField('Модель автомобиля', max_length=150)
    details = models.CharField('Поколение, годы, двигатель, ограничения', max_length=500, blank=True)
    configurations = models.JSONField('Модификации автомобиля по источнику', default=list, blank=True)
    source_url = models.URLField('Источник применимости')
    verified = models.BooleanField('Применимость проверена', default=False)

    class Meta:
        ordering = ['make', 'model', 'pk']
        constraints = [models.UniqueConstraint(fields=['offer', 'make', 'model', 'details'], name='procurement_vehicle_fitment')]

    def __str__(self):
        return f'{self.make} {self.model} {self.details}'.strip()


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        SENT = 'sent', 'Отправлен поставщику'
        CONFIRMED = 'confirmed', 'Подтверждён'
        PAID = 'paid', 'Оплачен'
        RECEIVED = 'received', 'Получено'
        CANCELLED = 'cancelled', 'Отменено'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    supplier_order_number = models.CharField(max_length=100, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    waybill_number = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    original_items = models.JSONField(default=list)
    request_key = models.UUIDField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    items = models.JSONField(default=list)
    estimated_total = models.DecimalField(max_digits=26, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['user', 'request_key'], name='procurement_purchase_request')]


    @property
    def order_number(self):
        return f'ЗК-{self.pk:06d}'

    @property
    def total(self):
        return sum((Decimal(str(item['cost'])) * item['quantity'] for item in self.items), Decimal(0))


class PurchaseDocument(models.Model):
    order = models.ForeignKey(PurchaseOrder, related_name='documents', on_delete=models.CASCADE)
    kind = models.CharField(max_length=12, choices=[('invoice', 'Счёт'), ('waybill', 'Накладная')])
    name = models.CharField(max_length=255)
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)


class KaspiSellerSnapshot(models.Model):
    offer = models.OneToOneField(Offer, related_name='kaspi_sellers', on_delete=models.CASCADE)
    source_url = models.URLField()
    city = models.CharField(max_length=20, default='750000000')
    checked_on = models.DateField()
    complete = models.BooleanField(default=False)
    sellers = models.JSONField(default=list)

    @property
    def ranked_sellers(self):
        return sorted(self.sellers, key=lambda seller: (Decimal(str(seller['price'])),
            seller.get('rating') is None, -Decimal(str(seller['rating'])) if seller.get('rating') is not None else Decimal(0),
            seller.get('reviews') is None, -(seller.get('reviews') or 0), seller['name'].casefold()))
