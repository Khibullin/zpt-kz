from django.contrib import admin
from django.db import transaction

from .models import (
    KaspiCompetitorIngestBatch,
    KaspiCompetitorOfferSnapshot,
    KaspiOwnPriceSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
    KaspiActiveExportRow,
)


@admin.register(KaspiActiveExportRow)
class KaspiActiveExportRowAdmin(admin.ModelAdmin):
    """Audited article corrections; source prices, stock and bindings are read-only."""
    list_display = ('master_sku', 'product_name', 'merchant_sku', 'price', 'quantity')
    search_fields = ('master_sku', 'product_name', 'merchant_sku')
    fields = ('batch', 'row_number', 'master_sku', 'product_name', 'brand',
              'merchant_sku', 'price', 'quantity', 'listing', 'competitor_scan_note')
    readonly_fields = tuple(field for field in fields if field != 'merchant_sku')

    def get_queryset(self, request):
        from .views import _latest_active_batch
        batch = _latest_active_batch()
        return super().get_queryset(request).filter(batch=batch) if batch else super().get_queryset(request).none()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if 'merchant_sku' in form.changed_data:
            # A changed identity invalidates the previous comparison only.
            for field in ('competitor_seller', 'competitor_seller_id', 'competitor_city',
                          'competitor_url', 'competitor_scan_status', 'candidate_title',
                          'candidate_url', 'candidate_seller'):
                setattr(obj, field, '')
            obj.competitor_min_price = obj.competitor_checked_at = obj.candidate_price = None
            obj.competitor_scan_note = 'Артикул уточнён вручную; повторите обработку сохранённого JSON'
            obj.verified_competitor_offers.all().delete()
            from .models import KaspiActiveCityCheck
            KaspiActiveCityCheck.objects.filter(active_row=obj).delete()
        super().save_model(request, obj, form, change)


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


@admin.register(KaspiCompetitorIngestBatch)
class KaspiCompetitorIngestBatchAdmin(ListingAdminMixin, admin.ModelAdmin):
    list_display = (
        "external_batch_id",
        "product_article",
        "source",
        "offer_count",
        "captured_at",
        "received_at",
    )
    search_fields = (
        "external_batch_id",
        "listing__product__article",
        "listing__master_sku",
    )
    readonly_fields = (
        "external_batch_id",
        "listing",
        "source",
        "captured_at",
        "offer_count",
        "received_at",
    )
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False
