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
