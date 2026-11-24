from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class KaspiRepricerRule(models.Model):
    class Mode(models.TextChoices):
        OBSERVE = "OBSERVE", "Наблюдение"
        MANUAL = "MANUAL", "С подтверждением"
        AUTO = "AUTO", "Автомат"

    listing = models.OneToOneField(
        "catalog.ProductKaspiListing",
        on_delete=models.CASCADE,
        related_name="repricer_rule",
        verbose_name="Kaspi-привязка",
    )
    mode = models.CharField(
        "Режим",
        max_length=16,
        choices=Mode.choices,
        default=Mode.OBSERVE,
    )
    min_price = models.DecimalField(
        "MIN цена",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Ни одна рекомендация не может быть ниже этой цены.",
    )
    min_margin_percent = models.DecimalField(
        "Минимальная маржа, %",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Зарезервировано для финансового слоя; MIN цена остаётся жёстким ограничением.",
    )
    price_step = models.DecimalField(
        "Шаг цены",
        max_digits=10,
        decimal_places=2,
        default=1,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    max_change_percent = models.DecimalField(
        "Макс. изменение за расчёт, %",
        max_digits=6,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    allow_raise = models.BooleanField("Разрешать повышение", default=True)
    is_enabled = models.BooleanField("Включён", default=True, db_index=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Правило репрайсера Kaspi"
        verbose_name_plural = "Правила репрайсера Kaspi"
        ordering = ("listing__product__article", "listing_id")

    def __str__(self):
        article = self.listing.product.article or f"product-{self.listing.product_id}"
        return f"{article}: {self.get_mode_display()}"


class KaspiCompetitorOfferSnapshot(models.Model):
    listing = models.ForeignKey(
        "catalog.ProductKaspiListing",
        on_delete=models.CASCADE,
        related_name="competitor_offer_snapshots",
        verbose_name="Kaspi-привязка",
    )
    seller_name = models.CharField("Продавец", max_length=255)
    seller_code = models.CharField("Код продавца", max_length=128, blank=True)
    price = models.DecimalField(
        "Цена",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    position = models.PositiveIntegerField("Позиция", null=True, blank=True)
    is_available = models.BooleanField("В наличии", default=True)
    source = models.CharField("Источник", max_length=64, default="manual")
    city_id = models.CharField("Город Kaspi (код)", max_length=12, blank=True, default="", db_index=True)
    captured_at = models.DateTimeField("Получено", db_index=True)

    class Meta:
        verbose_name = "Цена конкурента Kaspi"
        verbose_name_plural = "Цены конкурентов Kaspi"
        ordering = ("-captured_at", "price")
        indexes = [
            models.Index(fields=("listing", "captured_at"), name="repr_offer_time_idx"),
            models.Index(fields=("listing", "price"), name="repr_offer_price_idx"),
        ]

    def __str__(self):
        return f"{self.seller_name}: {self.price} ₸"


class KaspiCompetitorIngestBatch(models.Model):
    """Idempotency record for one collector POST of offer snapshots."""

    external_batch_id = models.UUIDField("Внешний batch id", unique=True)
    listing = models.ForeignKey(
        "catalog.ProductKaspiListing",
        on_delete=models.CASCADE,
        related_name="competitor_ingest_batches",
        verbose_name="Kaspi-привязка",
    )
    source = models.CharField("Источник", max_length=64, default="office_collector")
    city_id = models.CharField("Город Kaspi (код)", max_length=12, blank=True, default="", db_index=True)
    captured_at = models.DateTimeField("Получено")
    offer_count = models.PositiveIntegerField("Число офферов")
    received_at = models.DateTimeField("Принято", auto_now_add=True)

    class Meta:
        verbose_name = "Пакет цен конкурентов Kaspi"
        verbose_name_plural = "Пакеты цен конкурентов Kaspi"
        ordering = ("-received_at",)
        indexes = [
            models.Index(fields=("listing", "received_at"), name="repr_ingest_listing_idx"),
        ]

    def __str__(self):
        return f"{self.external_batch_id} × {self.offer_count}"


class KaspiOwnPriceSnapshot(models.Model):
    listing = models.ForeignKey(
        "catalog.ProductKaspiListing",
        on_delete=models.CASCADE,
        related_name="own_price_snapshots",
        verbose_name="Kaspi-привязка",
    )
    price = models.DecimalField(
        "Наша цена",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    source = models.CharField("Источник", max_length=64, default="sync")
    captured_at = models.DateTimeField("Получено", db_index=True)

    class Meta:
        verbose_name = "История нашей цены Kaspi"
        verbose_name_plural = "История наших цен Kaspi"
        ordering = ("-captured_at",)
        indexes = [
            models.Index(fields=("listing", "captured_at"), name="repr_own_time_idx"),
        ]

    def __str__(self):
        article = self.listing.product.article or f"product-{self.listing.product_id}"
        return f"{article}: {self.price} ₸"


class KaspiRepricerRecommendation(models.Model):
    class Action(models.TextChoices):
        LOWER = "LOWER", "Снизить"
        RAISE = "RAISE", "Повысить"
        HOLD = "HOLD", "Оставить"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Ожидает"
        APPLIED = "APPLIED", "Применено"
        DISMISSED = "DISMISSED", "Отклонено"

    listing = models.ForeignKey(
        "catalog.ProductKaspiListing",
        on_delete=models.CASCADE,
        related_name="repricer_recommendations",
        verbose_name="Kaspi-привязка",
    )
    rule = models.ForeignKey(
        KaspiRepricerRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recommendations",
        verbose_name="Правило",
    )
    current_price = models.DecimalField("Текущая цена", max_digits=14, decimal_places=2)
    best_competitor_price = models.DecimalField(
        "Лучшая цена конкурента",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    recommended_price = models.DecimalField(
        "Рекомендованная цена",
        max_digits=14,
        decimal_places=2,
    )
    market_position = models.PositiveIntegerField("Наша позиция", null=True, blank=True)
    action = models.CharField("Действие", max_length=16, choices=Action.choices)
    reason_code = models.CharField("Код причины", max_length=64)
    reason = models.CharField("Причина", max_length=500)
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField("Закрыто", null=True, blank=True)

    class Meta:
        verbose_name = "Рекомендация репрайсера Kaspi"
        verbose_name_plural = "Рекомендации репрайсера Kaspi"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("listing", "created_at"), name="repr_rec_time_idx"),
        ]

    def __str__(self):
        article = self.listing.product.article or f"product-{self.listing.product_id}"
        return f"{article}: {self.current_price} → {self.recommended_price} ₸"


class KaspiManualCollectionRun(models.Model):
    """Browser-driven, resumable manual collection; never changes product prices."""

    class Status(models.TextChoices):
        RUNNING = "running", "В процессе"
        DONE = "done", "Завершено"
        STOPPED = "stopped", "Остановлено"

    created_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="kaspi_manual_collections",
    )
    city_ids = models.JSONField("Города", default=list)
    listing_ids = models.JSONField("Kaspi-привязки", default=list)
    status = models.CharField("Состояние", max_length=12, choices=Status.choices, default=Status.RUNNING)
    next_index = models.PositiveIntegerField("Следующее задание", default=0)
    saved_offers = models.PositiveIntegerField("Предложений сохранено", default=0)
    empty_count = models.PositiveIntegerField("Без предложений", default=0)
    skipped_count = models.PositiveIntegerField("Без привязки", default=0)
    error_count = models.PositiveIntegerField("Ошибок", default=0)
    last_error = models.CharField("Последняя ошибка", max_length=500, blank=True)
    started_at = models.DateTimeField("Начато", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    finished_at = models.DateTimeField("Завершено", null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)
        verbose_name = "Ручной сбор предложений Kaspi"
        verbose_name_plural = "Ручные сборы предложений Kaspi"

    @property
    def total(self):
        return len(self.city_ids) * len(self.listing_ids)


class KaspiManualPriceDraft(models.Model):
    """Saved manual price for later review/export. Never writes to Kaspi."""
    listing = models.OneToOneField(
        "catalog.ProductKaspiListing", on_delete=models.CASCADE,
        related_name="manual_price_draft",
    )
    price = models.PositiveIntegerField("Подготовленная цена")
    updated_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Черновик цены Kaspi"
        verbose_name_plural = "Черновики цен Kaspi"


class KaspiActiveExportRow(models.Model):
    """One actual row of an uploaded Kaspi ACTIVE export, including unmatched SKU.

    Latest *completed* import batch with these rows defines the repicing universe.
    Never create catalog products implicitly from an unmatched row.
    """
    batch = models.ForeignKey(
        "catalog.CatalogImportBatch", on_delete=models.CASCADE,
        related_name="repricer_active_rows",
    )
    row_number = models.PositiveIntegerField()
    master_sku = models.CharField(max_length=128, blank=True, db_index=True)
    merchant_sku = models.CharField(max_length=128, blank=True)
    product_name = models.CharField(max_length=500, blank=True)
    brand = models.CharField(max_length=150, blank=True)
    price = models.PositiveIntegerField(null=True, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    listing = models.ForeignKey(
        "catalog.ProductKaspiListing", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="active_export_rows",
    )
    status = models.CharField(max_length=40, db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    competitor_min_price = models.PositiveIntegerField(null=True, blank=True)
    competitor_seller = models.CharField(max_length=255, blank=True)
    competitor_seller_id = models.CharField(max_length=128, blank=True)
    competitor_city = models.CharField(max_length=80, blank=True)
    competitor_url = models.URLField(max_length=500, blank=True)
    competitor_checked_at = models.DateTimeField(null=True, blank=True)
    competitor_scan_status = models.CharField(max_length=40, blank=True)
    competitor_scan_note = models.CharField(max_length=255, blank=True)
    candidate_price = models.PositiveIntegerField(null=True, blank=True)
    candidate_title = models.CharField(max_length=500, blank=True)
    candidate_url = models.URLField(max_length=500, blank=True)
    candidate_seller = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Строка выгрузки ACTIVE Kaspi"
        verbose_name_plural = "Строки выгрузки ACTIVE Kaspi"
        ordering = ("row_number", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "row_number"), name="repr_active_batch_row_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("batch", "status"), name="repr_active_batch_status_idx"),
        ]


class KaspiRepricerSearchSettings(models.Model):
    """Persisted operator choices for producing an Apify input file.

    These settings only affect the generated JSON, never Kaspi or our prices.
    """
    cities = models.JSONField(default=list, verbose_name="Города Kaspi")
    cards_per_product = models.PositiveSmallIntegerField(default=3, verbose_name="Карточек на запрос")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки поиска цен Kaspi"
        verbose_name_plural = "Настройки поиска цен Kaspi"


class KaspiActiveCompetitorOffer(models.Model):
    """Verified offer from a manually uploaded Apify dataset."""
    active_row = models.ForeignKey(
        KaspiActiveExportRow, on_delete=models.CASCADE,
        related_name="verified_competitor_offers",
    )
    seller_id = models.CharField(max_length=128)
    seller_name = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=80)
    price = models.PositiveIntegerField()
    product_url = models.URLField(max_length=500, blank=True)
    checked_at = models.DateTimeField()

    class Meta:
        ordering = ("price", "pk")
        indexes = [
            models.Index(fields=("active_row", "price"), name="repr_active_offer_price_idx"),
        ]
