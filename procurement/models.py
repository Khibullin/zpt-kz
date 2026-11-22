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
    supplier_checked_on = models.DateField(null=True, blank=True)
    kaspi_price_checked_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['supplier', 'brand', 'article'], name='procurement_supplier_article')]
        indexes = [models.Index(fields=['supplier', 'category'], name='proc_offer_supplier_category'),
                   models.Index(fields=['supplier', 'id'], include=['cost', 'kaspi_price'], name='proc_offer_catalog_rank')]
        ordering = ['brand', 'article', 'pk']


class ProfitableOfferRecord(models.Model):
    """Immutable first qualifying calculation; preserve it through later price uploads.

    Historical processing dates are nullable: old catalogue rows do not prove
    when the first profitability calculation actually occurred.
    """
    offer = models.OneToOneField(Offer, on_delete=models.PROTECT, related_name='profitable_record')
    source_cost = models.DecimalField(max_digits=14, decimal_places=2)
    source_kaspi_price = models.DecimalField(max_digits=14, decimal_places=2)
    source_profit = models.DecimalField(max_digits=18, decimal_places=2)
    source_roi = models.DecimalField(max_digits=18, decimal_places=4)
    commission = models.DecimalField(max_digits=5, decimal_places=2)
    expenses = models.DecimalField(max_digits=12, decimal_places=2)
    supplier_date = models.DateField(null=True, blank=True)
    kaspi_date = models.DateField(null=True, blank=True)
    captured_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    first_batch = models.ForeignKey('PriceUpdate', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='new_profitable_offers')
    initial_import = models.BooleanField(default=False)

    class Meta:
        ordering = ['offer_id']


class RetailListing(models.Model):
    catalog_key = models.CharField(max_length=250, null=True, blank=True, unique=True)
    offer = models.OneToOneField(Offer, on_delete=models.PROTECT, related_name='retail_listing')
    product = models.OneToOneField('catalog.Product', on_delete=models.PROTECT, related_name='omega_listing')
    pack_size = models.PositiveIntegerField(default=1)
    source_content = models.JSONField(default=dict)
    enabled = models.BooleanField(default=True)
    hold_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Preferences(models.Model):
    difference_min_profit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    best_min_profit = models.DecimalField(max_digits=12, decimal_places=2, default=1000, validators=[MinValueValidator(1000)])
    no_sellers_days = models.PositiveIntegerField(default=7, validators=[MinValueValidator(1), MaxValueValidator(30)])
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    commission = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])
    expenses = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    min_profit = models.DecimalField(max_digits=12, decimal_places=2, default=1000, validators=[MinValueValidator(0)])
    min_markup = models.DecimalField(max_digits=7, decimal_places=2, default=30, validators=[MinValueValidator(0)])


class WholesaleSelectionRule(models.Model):
    """Shared, explicitly saved preselection settings; never edits supplier source data."""
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True)
    supplier = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL)
    min_cost = models.DecimalField(max_digits=14, decimal_places=2, default=2000)
    max_vehicle_age = models.PositiveSmallIntegerField(default=20)
    category_groups = models.JSONField(default=list, blank=True)
    in_stock_only = models.BooleanField(default=True)
    classification_mode = models.CharField(max_length=12, default='auto')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)


class KaspiBuyingCandidate(models.Model):
    """Current Apify observation for buying comparison, not a verified Kaspi identity."""
    product_id = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=500)
    brand = models.CharField(max_length=120, blank=True)
    url = models.URLField(max_length=2048, blank=True)
    minimum_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    city_offers = models.JSONField(default=list, blank=True)
    offer = models.ForeignKey('Offer', null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='buying_candidates')
    match_status = models.CharField(max_length=24, default='unmatched')
    source_filename = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-imported_at', 'pk']


class KaspiPriceAudit(models.Model):
    """Latest observed *seller* minimum per Omega SKU and Apify export.

    Deliberately separate from immutable PermanentBuyingItem and source Offer.
    A matching brand/article is not verification of vehicle/part execution.
    """
    offer = models.ForeignKey('Offer', on_delete=models.PROTECT,
                              related_name='kaspi_price_audits')
    source_filename = models.CharField(max_length=255)
    min_price = models.DecimalField(max_digits=14, decimal_places=2)
    product_id = models.CharField(max_length=80, blank=True)
    card_url = models.URLField(max_length=2048, blank=True)
    card_title = models.CharField(max_length=500, blank=True)
    seller_name = models.CharField(max_length=180, blank=True)
    cost_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    commission_snapshot = models.DecimalField(max_digits=6, decimal_places=2)
    expenses_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    profit = models.DecimalField(max_digits=18, decimal_places=2)
    roi = models.DecimalField(max_digits=18, decimal_places=4)
    card_ids = models.JSONField(default=list, blank=True)
    # Price evidence from seller does NOT prove the execution or fitment.
    identity_status = models.CharField(max_length=24, default='needs_review')
    observed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['offer', 'source_filename'], name='proc_kaspi_audit_offer_file')]
        indexes = [models.Index(fields=['offer', '-observed_at'],
                                name='proc_audit_offer_latest')]


class PermanentBuyingItem(models.Model):
    """Append-only buying register. No automatic updates or deletes.

    Each supplier SKU is saved once; financial data is frozen at admission.
    Existing source Offer and purchasing Selection records are never changed.
    """
    offer = models.OneToOneField('Offer', on_delete=models.PROTECT,
                                 related_name='permanent_buying_item')
    source = models.CharField(max_length=16, default='historical')
    product_id = models.CharField(max_length=80, blank=True)
    title = models.CharField(max_length=500)
    kaspi_url = models.URLField(max_length=2048, blank=True)
    cost_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    kaspi_price_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    commission_snapshot = models.DecimalField(max_digits=6, decimal_places=2)
    expenses_snapshot = models.DecimalField(max_digits=14, decimal_places=2)
    profit_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    roi_snapshot = models.DecimalField(max_digits=18, decimal_places=4)
    identity_status = models.CharField(max_length=24, default='historical')
    photo_url = models.URLField(max_length=2048, blank=True)
    included_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-profit_snapshot', '-roi_snapshot', 'pk']



class BuyingComparisonMaterial(models.Model):
    """Source-separated, independently fetched card information, never money facts.

    One record per supplier offer, Apify export and selected cheapest Kaspi card.
    A different price export cannot silently reuse a different card's description.
    """
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name='comparison_materials')
    source_filename = models.CharField(max_length=255, blank=True)
    kaspi_product_id = models.CharField(max_length=80, blank=True)
    supplier_url = models.URLField(max_length=2048, blank=True)
    kaspi_url = models.URLField(max_length=2048, blank=True)
    supplier_data = models.JSONField(default=dict, blank=True)
    kaspi_data = models.JSONField(default=dict, blank=True)
    supplier_status = models.CharField(max_length=24, default='pending')
    kaspi_status = models.CharField(max_length=24, default='pending')
    supplier_error = models.CharField(max_length=250, blank=True)
    kaspi_error = models.CharField(max_length=250, blank=True)
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['offer', 'source_filename', 'kaspi_product_id'],
            name='proc_comparison_offer_export_card')]
        indexes = [models.Index(fields=['offer', 'source_filename'],
                                name='proc_cmp_offer_source')]



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
    source_url = models.URLField('Источник применимости', max_length=2048)
    verified = models.BooleanField('Применимость проверена', default=False)

    class Meta:
        ordering = ['make', 'model', 'pk']
        indexes = [models.Index(fields=['verified', 'make', 'model', 'offer'], name='proc_fitment_filter_options')]
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
    title = models.CharField('Название закупки', max_length=200, blank=True)
    expected_date = models.DateField('Плановая дата доставки', null=True, blank=True)
    destination = models.CharField('Склад поставки', max_length=3, choices=[('PP2','PP2 · Rapida'),('PP1','PP1 · Asia Grade')], default='PP2')
    delivery_stage = models.CharField('Этап поставки', max_length=12, choices=[('draft','Черновик'),('sent','Отправлен поставщику'),('waiting','Ожидается'),('shipped','В пути'),('cancelled','Отменён')], default='draft')
    archived = models.BooleanField(default=False)
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
    def display_title(self):
        return self.title or f'{self.supplier.name} · {self.order_number}' + (f' · поставка {self.expected_date:%d.%m.%Y}' if self.expected_date else '')

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


class KaspiCardSearch(models.Model):
    """A reviewed search outcome, never inferred from a missing product URL."""
    offer = models.OneToOneField(Offer, related_name='kaspi_card_search', on_delete=models.CASCADE)
    checked_on = models.DateField()
    city = models.CharField(max_length=20, default='750000000')
    complete = models.BooleanField(default=False)
    checked_brand = models.CharField(max_length=100)
    checked_article = models.CharField(max_length=120)
    checked_name = models.CharField(max_length=500)
    checks = models.JSONField(default=list)




class SupplierComparisonPriceList(models.Model):
    """Independent supplier price snapshot used only by the comparison screen.

    Uploading here never changes Offer, PriceUpdate, procurement selections or Kaspi tasks.
    """
    supplier_code = models.CharField(max_length=24, unique=True)
    filename = models.CharField(max_length=255)
    checked_on = models.DateField()
    uploaded_at = models.DateTimeField(auto_now=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    row_count = models.PositiveIntegerField(default=0)
    available_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    rows = models.JSONField(default=list)

    class Meta:
        ordering = ['supplier_code']


class PriceUpdate(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    checked_on = models.DateField()
    full_catalog = models.BooleanField(default=False)
    rows = models.JSONField(default=list)
    row_count = models.PositiveIntegerField(default=0)
    cursor = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default='ready', choices=[
        ('ready', 'Готов к обработке'), ('running', 'Обновление каталога'),
        ('done', 'Каталог обновлён')])
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['supplier'],
            condition=models.Q(status__in=['ready', 'running']), name='one_active_price_update')]
        ordering = ['-pk']


class KaspiReviewTask(models.Model):
    offer = models.OneToOneField(Offer, on_delete=models.CASCADE, related_name='review_task')
    batch = models.ForeignKey(PriceUpdate, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=250)
    priority = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(max_length=20, default='pending', choices=[
        ('pending', 'Требует проверки'), ('checking', 'Проверяется'), ('blocked', 'Источник недоступен'), ('done', 'Проверено')])
    updated_at = models.DateTimeField(auto_now=True)


class RetailPublicationRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cursor = models.PositiveIntegerField(default=0)
    max_offer_id = models.PositiveIntegerField(default=0)
    processed = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, default='running')
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


from .kaspi_models import KaspiPreparedCard, KaspiCardImage, KaspiCardPrice, KaspiPricingRule, KaspiCardJob, KaspiPriceFeed  # noqa: E402,F401
