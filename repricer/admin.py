from django.contrib import admin

from .models import (
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)


class ListingAdminMixin:
    list_select_related = ("listing__product",)

    @admin.display(description="Артикул", ordering="listing__product__article")
    def product_article(self, obj):
        return obj.listing.product.article or f"product-{obj.listing.product_id}"

    @admin.display(description="Товар", ordering="listing__product__title")
    def product_title(self, obj):
        return obj.listing.product.title

    @admin.display(description="Kaspi master SKU", ordering="listing__master_sku")
    def kaspi_master_sku(self, obj):
        return obj.listing.master_sku


@admin.register(KaspiRepricerRule)
class KaspiRepricerRuleAdmin(ListingAdminMixin, admin.ModelAdmin):
    list_display = (
        "product_article",
        "product_title",
        "kaspi_master_sku",
        "current_kaspi_price",
        "min_price",
        "price_step",
        "max_change_percent",
        "mode",
        "is_enabled",
        "updated_at",
    )
    list_filter = ("mode", "is_enabled", "allow_raise")
    search_fields = (
        "listing__product__article",
        "listing__product__title",
        "listing__master_sku",
        "listing__merchant_sku",
        "listing__barcode",
    )
    autocomplete_fields = ("listing",)

    @admin.display(description="Наша цена Kaspi", ordering="listing__last_known_our_price")
    def current_kaspi_price(self, obj):
        return obj.listing.last_known_our_price or "—"


@admin.register(KaspiCompetitorOfferSnapshot)
class KaspiCompetitorOfferSnapshotAdmin(ListingAdminMixin, admin.ModelAdmin):
    list_display = (
        "product_article",
        "kaspi_master_sku",
        "seller_name",
        "price",
        "position",
        "is_available",
        "source",
        "captured_at",
    )
    list_filter = ("source", "is_available", "captured_at")
    search_fields = (
        "listing__product__article",
        "listing__product__title",
        "listing__master_sku",
        "listing__merchant_sku",
        "seller_name",
        "seller_code",
    )
    autocomplete_fields = ("listing",)
    date_hierarchy = "captured_at"


@admin.register(KaspiOwnPriceSnapshot)
class KaspiOwnPriceSnapshotAdmin(ListingAdminMixin, admin.ModelAdmin):
    list_display = (
        "product_article",
        "kaspi_master_sku",
        "price",
        "source",
        "captured_at",
    )
    list_filter = ("source", "captured_at")
    search_fields = (
        "listing__product__article",
        "listing__product__title",
        "listing__master_sku",
        "listing__merchant_sku",
    )
    autocomplete_fields = ("listing",)
    date_hierarchy = "captured_at"


@admin.register(KaspiRepricerRecommendation)
class KaspiRepricerRecommendationAdmin(ListingAdminMixin, admin.ModelAdmin):
    list_display = (
        "product_article",
        "kaspi_master_sku",
        "current_price",
        "best_competitor_price",
        "market_position",
        "recommended_price",
        "action",
        "status",
        "created_at",
    )
    list_filter = ("action", "status", "created_at")
    search_fields = (
        "listing__product__article",
        "listing__product__title",
        "listing__master_sku",
        "listing__merchant_sku",
        "reason_code",
        "reason",
    )
    readonly_fields = (
        "listing",
        "rule",
        "current_price",
        "best_competitor_price",
        "recommended_price",
        "market_position",
        "action",
        "reason_code",
        "reason",
        "created_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
