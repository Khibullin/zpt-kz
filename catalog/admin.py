from django.contrib import admin
from .models import (
    Country,
    Brand,
    CarModel,
    Category,
    Product,
    SellerProfile,
    SellerWholesaleTerms,
    ProductImage,
    ProductPriceTier,
    ProductPromotion,
    ProductConsignment,
    ProductConsignmentRequest,
    ProductFulfillment,
    ProductBarcode,
    ProductKaspiListing,
    CatalogImportBatch,
    CatalogImportItem,
)


admin.site.site_header = 'Администрирование ZPT Market'
admin.site.site_title = 'ZPT Market'
admin.site.index_title = 'Панель управления маркетом'


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'country',
    )

    list_filter = ('country',)

    search_fields = (
        'name',
        'country__name',
    )

    ordering = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'brand',
        'get_country',
    )

    list_filter = (
        'brand__country',
        'brand',
    )

    search_fields = (
        'name',
        'brand__name',
        'brand__country__name',
    )

    ordering = ('name',)

    def get_country(self, obj):
        return obj.brand.country.name
    get_country.short_description = 'Страна'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
    )

    search_fields = ('name',)
    ordering = ('name',)


class SellerWholesaleTermsInline(admin.StackedInline):
    model = SellerWholesaleTerms
    extra = 0
    max_num = 1
    can_delete = True
    fieldsets = (
        ('НДС и оплата', {
            'fields': (
                'vat_mode',
                'prepayment_percent',
                'confirm_stock_before_payment',
                'stock_note',
            ),
        }),
        ('Документы', {
            'fields': (
                'provides_invoice',
                'provides_waybill',
                'provides_esf',
            ),
        }),
        ('Самовывоз и доставка', {
            'fields': (
                'pickup_enabled',
                'pickup_city',
                'delivery_kz_enabled',
                'delivery_payer',
                'primary_carrier',
                'primary_carrier_service',
                'primary_carrier_url',
                'other_carrier_allowed',
            ),
        }),
    )


@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'phone',
        'city',
        'wholesale_enabled',
        'wholesale_min_order_qty',
        'user',
    )

    search_fields = (
        'name',
        'phone',
        'city',
        'user__username',
    )

    list_filter = ('city', 'wholesale_enabled')

    ordering = ('name',)
    inlines = [SellerWholesaleTermsInline]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ('image', 'sort_order', 'is_primary')
    ordering = ('sort_order', 'id')


class ProductPriceTierInline(admin.TabularInline):
    model = ProductPriceTier
    extra = 0
    fields = ('min_qty', 'price', 'is_active')
    ordering = ('min_qty', 'id')


class ProductPromotionInline(admin.TabularInline):
    model = ProductPromotion
    extra = 0
    fields = (
        'promotion_type',
        'price',
        'starts_at',
        'ends_at',
        'qty_limit',
        'is_active',
    )


class ProductConsignmentInline(admin.StackedInline):
    model = ProductConsignment
    extra = 0
    max_num = 1
    fields = (
        'enabled',
        'max_qty',
        'settlement_price',
        'term_days',
        'conditions',
    )


class ProductFulfillmentInline(admin.StackedInline):
    model = ProductFulfillment
    extra = 0
    max_num = 1
    fields = ('external_id', 'source', 'last_synced_at')


class ProductBarcodeInline(admin.TabularInline):
    model = ProductBarcode
    extra = 0
    fields = ('code', 'source', 'is_primary')


class ProductKaspiListingInline(admin.TabularInline):
    model = ProductKaspiListing
    extra = 0
    fields = (
        'master_sku',
        'merchant_sku',
        'barcode',
        'is_active',
        'publish_to_kaspi',
        'last_known_our_price',
        'last_synced_at',
    )


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'image',
        'sort_order',
        'is_primary',
    )

    list_editable = (
        'sort_order',
        'is_primary',
    )

    search_fields = (
        'product__title',
        'product__article',
    )

    ordering = (
        'product',
        'sort_order',
        'id',
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'article',
        'price',
        'price_on_request',
        'cost_price',
        'stock_qty',
        'seller_profile',
        'seller_name',
        'whatsapp_number',
        'city',
        'brand',
        'car_model',
        'category',
        'condition',
        'status',
        'publish_to_sellers',
        'publish_to_kaspi',
        'created_at',
    )

    list_filter = (
        'status',
        'condition',
        'price_on_request',
        'brand__country',
        'brand',
        'car_model',
        'category',
        'city',
        'seller_profile',
        'publish_to_sellers',
        'publish_to_kaspi',
    )

    search_fields = (
        'title',
        'article',
        'seller_name',
        'whatsapp_number',
        'description',
        'compatibility',
        'engine_compatibility',
        'oem_cross_references',
        'seller_profile__name',
    )

    autocomplete_fields = (
        'seller_profile',
        'brand',
        'car_model',
        'category',
    )

    readonly_fields = (
        'created_at',
        'updated_at',
    )

    inlines = (
        ProductImageInline,
        ProductFulfillmentInline,
        ProductBarcodeInline,
        ProductKaspiListingInline,
        ProductPriceTierInline,
        ProductPromotionInline,
        ProductConsignmentInline,
    )

    ordering = (
        '-created_at',
    )

    fieldsets = (
        (
            'Основная информация',
            {
                'fields': (
                    'title',
                    'article',
                    'category',
                    'price',
                    'price_on_request',
                    'condition',
                    'status',
                    'publish_to_sellers',
                    'publish_to_kaspi',
                )
            }
        ),

        (
            'Склад и учёт',
            {
                'fields': (
                    'seller_profile',
                    'cost_price',
                    'stock_qty',
                ),
                'description': (
                    'Себестоимость видна только в админке '
                    'и не публикуется на сайте.'
                ),
            }
        ),

        (
            'Автомобиль',
            {
                'fields': (
                    'brand',
                    'car_model',
                    'selected_brands',
                    'selected_models',
                    'compatibility',
                    'engine_compatibility',
                    'oem_cross_references',
                )
            }
        ),

        (
            'Продавец',
            {
                'fields': (
                    'seller_name',
                    'whatsapp_number',
                    'city',
                )
            }
        ),

        (
            'Описание и фото',
            {
                'fields': (
                    'description',
                    'main_image',
                )
            }
        ),

        (
            'Системная информация',
            {
                'fields': (
                    'created_at',
                    'updated_at',
                )
            }
        ),
    )


@admin.register(ProductConsignmentRequest)
class ProductConsignmentRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'product_article',
        'seller_profile',
        'requested_qty',
        'settlement_price',
        'term_days',
        'status',
        'created_at',
    )
    list_filter = (
        'status',
        'created_at',
    )
    search_fields = (
        'product__title',
        'product__article',
        'seller_profile__name',
        'seller_profile__phone',
    )
    list_select_related = (
        'product',
        'seller_profile',
    )
    readonly_fields = (
        'product',
        'seller_profile',
        'requested_qty',
        'settlement_price',
        'term_days',
        'conditions',
        'created_at',
        'updated_at',
    )
    fields = (
        'status',
        'product',
        'seller_profile',
        'requested_qty',
        'settlement_price',
        'term_days',
        'conditions',
        'created_at',
        'updated_at',
    )

    @admin.display(description='Артикул')
    def product_article(self, obj):
        return obj.product.article or '—'


class CatalogHistoryMixin:
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductFulfillment)
class ProductFulfillmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'external_id', 'source', 'last_synced_at')
    search_fields = ('external_id', 'product__article', 'product__title')
    autocomplete_fields = ('product',)
    list_filter = ('source',)


@admin.register(ProductBarcode)
class ProductBarcodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'code', 'source', 'is_primary', 'updated_at')
    search_fields = ('code', 'product__article', 'product__title')
    list_filter = ('source', 'is_primary')
    autocomplete_fields = ('product',)


@admin.register(ProductKaspiListing)
class ProductKaspiListingAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'master_sku',
        'merchant_sku',
        'is_active',
        'publish_to_kaspi',
        'last_known_our_price',
        'last_synced_at',
    )
    search_fields = (
        'master_sku',
        'merchant_sku',
        'barcode',
        'product__article',
        'product__title',
    )
    list_filter = ('is_active', 'publish_to_kaspi')
    autocomplete_fields = ('product',)
    readonly_fields = ('created_at', 'updated_at')


class CatalogImportItemInline(admin.TabularInline):
    model = CatalogImportItem
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ('article', 'action', 'product', 'warnings', 'errors', 'changed_fields')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CatalogImportBatch)
class CatalogImportBatchAdmin(CatalogHistoryMixin, admin.ModelAdmin):
    list_display = (
        'id',
        'seller_profile',
        'source',
        'source_scope',
        'status',
        'archive_status',
        'filename',
        'source_unique_count',
        'selected_count',
        'created_count',
        'updated_count',
        'missing_from_source_count',
        'started_at',
    )
    list_filter = ('status', 'archive_status', 'source_scope', 'source', 'mode')
    search_fields = ('filename', 'file_sha256', 'seller_profile__name')
    readonly_fields = (
        'seller_profile',
        'source',
        'filename',
        'file_sha256',
        'source_archive_path',
        'archive_status',
        'archive_error',
        'started_at',
        'finished_at',
        'mode',
        'source_scope',
        'status',
        'source_row_count',
        'source_unique_count',
        'selected_count',
        'created_count',
        'updated_count',
        'unchanged_count',
        'skipped_count',
        'conflict_count',
        'warning_count',
        'error_count',
        'missing_from_source_count',
        'previous_successful_batch',
        'blocked_reason',
        'allow_source_shrink_reason',
    )
    inlines = (CatalogImportItemInline,)
    date_hierarchy = 'started_at'


@admin.register(CatalogImportItem)
class CatalogImportItemAdmin(CatalogHistoryMixin, admin.ModelAdmin):
    list_display = ('id', 'batch', 'article', 'action', 'product')
    list_filter = ('action',)
    search_fields = ('article', 'batch__filename')
    readonly_fields = (
        'batch',
        'product',
        'article',
        'action',
        'warnings',
        'errors',
        'changed_fields',
    )
