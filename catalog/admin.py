from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from catalog.product_photo_import import (
    STALE_APPLY_MESSAGE as PHOTO_STALE_APPLY_MESSAGE,
    PhotoImportBlockedError,
    PhotoImportError,
    PhotoImportStaleError,
    PhotoImportUploadForm,
    apply_photo_import_preview,
    persist_photo_import_batch,
    persist_photo_zip,
    plan_product_photo_import,
    preview_photo_display_rows,
    safe_upload_filename as photo_zip_filename,
    sha256_bytes as photo_sha256_bytes,
)
from catalog.wholesale_export import XLSX_CONTENT_TYPE
from catalog.wholesale_update import (
    STALE_APPLY_MESSAGE,
    WholesaleUpdateBlockedError,
    WholesaleUpdateError,
    WholesaleUpdateStaleError,
    WholesaleUpdateUploadForm,
    apply_wholesale_update_preview,
    persist_wholesale_update_batch,
    plan_wholesale_update,
    preview_display_rows,
    safe_upload_filename,
    sha256_bytes,
    wholesale_update_filename,
    wholesale_update_xlsx_bytes,
)
from catalog.stock_service import set_stock_quantity
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
    KaspiListingFactSnapshot,
    KaspiOrder,
    KaspiSalesOperation,
    KaspiEconomicsConfig,
    ProductKaspiEconomicsPolicy,
    CatalogImportBatch,
    CatalogImportItem,
    Warehouse,
    ProductWarehouseStock,
    StockMovement,
    MaintenanceKit,
    MaintenanceKitItem,
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
        'wholesale_update_link',
        'photo_import_link',
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
    change_form_template = 'admin/catalog/sellerprofile/change_form.html'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/wholesale-update/',
                self.admin_site.admin_view(self.wholesale_update_view),
                name='catalog_sellerprofile_wholesale_update',
            ),
            path(
                '<path:object_id>/wholesale-update/download/',
                self.admin_site.admin_view(self.wholesale_download_view),
                name='catalog_sellerprofile_wholesale_download',
            ),
            path(
                '<path:object_id>/wholesale-update/<int:batch_id>/',
                self.admin_site.admin_view(self.wholesale_preview_view),
                name='catalog_sellerprofile_wholesale_preview',
            ),
            path(
                '<path:object_id>/wholesale-update/<int:batch_id>/apply/',
                self.admin_site.admin_view(self.wholesale_apply_view),
                name='catalog_sellerprofile_wholesale_apply',
            ),
            path(
                '<path:object_id>/photo-import/',
                self.admin_site.admin_view(self.photo_import_view),
                name='catalog_sellerprofile_photo_import',
            ),
            path(
                '<path:object_id>/photo-import/<int:batch_id>/',
                self.admin_site.admin_view(self.photo_preview_view),
                name='catalog_sellerprofile_photo_preview',
            ),
            path(
                '<path:object_id>/photo-import/<int:batch_id>/apply/',
                self.admin_site.admin_view(self.photo_apply_view),
                name='catalog_sellerprofile_photo_apply',
            ),
        ]
        return custom + urls

    def _can_wholesale_update(self, request):
        user = request.user
        return bool(
            user.is_active
            and user.is_staff
            and (
                user.is_superuser
                or user.has_perm('catalog.change_product')
                or user.has_perm('catalog.change_sellerprofile')
            )
        )

    def _seller_or_404(self, object_id):
        return get_object_or_404(SellerProfile, pk=object_id)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context.update({
            'wholesale_update_url': reverse(
                'admin:catalog_sellerprofile_wholesale_update',
                args=[object_id],
            ),
            'wholesale_download_url': reverse(
                'admin:catalog_sellerprofile_wholesale_download',
                args=[object_id],
            ),
            'photo_import_url': reverse(
                'admin:catalog_sellerprofile_photo_import',
                args=[object_id],
            ),
        })
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context
        )

    @admin.display(description='Цены и остатки')
    def wholesale_update_link(self, obj):
        url = reverse('admin:catalog_sellerprofile_wholesale_update', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Обновить цены и остатки</a>',
            url,
        )

    @admin.display(description='Фото ZIP')
    def photo_import_link(self, obj):
        url = reverse('admin:catalog_sellerprofile_photo_import', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Загрузить фотографии ZIP</a>',
            url,
        )

    def wholesale_download_view(self, request, object_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        seller = self._seller_or_404(object_id)
        payload = wholesale_update_xlsx_bytes(seller)
        filename = wholesale_update_filename(seller)
        response = HttpResponse(payload, content_type=XLSX_CONTENT_TYPE)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    def wholesale_update_view(self, request, object_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        seller = self._seller_or_404(object_id)
        form = WholesaleUpdateUploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            uploaded = form.cleaned_data['file']
            payload = uploaded.read()
            filename = safe_upload_filename(getattr(uploaded, 'name', ''))
            try:
                rows = plan_wholesale_update(seller, payload)
            except WholesaleUpdateError as exc:
                form.add_error('file', str(exc))
            else:
                batch, _summary = persist_wholesale_update_batch(
                    seller=seller,
                    rows=rows,
                    filename=filename,
                    file_sha256=sha256_bytes(payload),
                    mode=CatalogImportBatch.MODE_DRY_RUN,
                    uploaded_by=request.user,
                )
                return redirect(
                    'admin:catalog_sellerprofile_wholesale_preview',
                    object_id,
                    batch.pk,
                )
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'form': form,
            'title': f'Обновить цены и остатки: {seller.name}',
            'download_url': reverse(
                'admin:catalog_sellerprofile_wholesale_download',
                args=[seller.pk],
            ),
        }
        return render(
            request,
            'admin/catalog/sellerprofile/wholesale_update.html',
            context,
        )

    def wholesale_preview_view(self, request, object_id, batch_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        seller = self._seller_or_404(object_id)
        batch = get_object_or_404(
            CatalogImportBatch,
            pk=batch_id,
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_WHOLESALE_UPDATE,
            mode=CatalogImportBatch.MODE_DRY_RUN,
        )
        rows = preview_display_rows(batch)
        summary = {
            'rows': batch.source_row_count,
            'matched': batch.updated_count + batch.unchanged_count,
            'updated': batch.updated_count,
            'unchanged': batch.unchanged_count,
            'conflicts': batch.conflict_count,
            'errors': batch.error_count,
            'has_blockers': bool(batch.conflict_count or batch.error_count),
            'has_warnings': bool(batch.warning_count),
        }
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'batch': batch,
            'rows': rows,
            'summary': summary,
            'title': f'Предпросмотр: {seller.name}',
            'apply_url': reverse(
                'admin:catalog_sellerprofile_wholesale_apply',
                args=[seller.pk, batch.pk],
            ),
            'upload_url': reverse(
                'admin:catalog_sellerprofile_wholesale_update',
                args=[seller.pk],
            ),
        }
        return render(
            request,
            'admin/catalog/sellerprofile/wholesale_update_preview.html',
            context,
        )

    def wholesale_apply_view(self, request, object_id, batch_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        if request.method != 'POST':
            return redirect(
                'admin:catalog_sellerprofile_wholesale_preview',
                object_id,
                batch_id,
            )
        seller = self._seller_or_404(object_id)
        batch = get_object_or_404(
            CatalogImportBatch,
            pk=batch_id,
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_WHOLESALE_UPDATE,
            mode=CatalogImportBatch.MODE_DRY_RUN,
        )
        if request.POST.get('confirm') != '1':
            messages.error(request, 'Подтвердите применение изменений.')
            return redirect(
                'admin:catalog_sellerprofile_wholesale_preview',
                seller.pk,
                batch.pk,
            )
        try:
            write_batch, summary = apply_wholesale_update_preview(
                seller=seller,
                preview_batch=batch,
                applied_by=request.user,
            )
        except WholesaleUpdateStaleError:
            messages.error(request, STALE_APPLY_MESSAGE)
            return redirect(
                'admin:catalog_sellerprofile_wholesale_update',
                seller.pk,
            )
        except WholesaleUpdateBlockedError as exc:
            messages.error(request, str(exc))
            return redirect(
                'admin:catalog_sellerprofile_wholesale_preview',
                seller.pk,
                batch.pk,
            )
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'batch': write_batch,
            'summary': summary,
            'title': 'Изменения применены',
            'history_url': reverse(
                'admin:catalog_catalogimportbatch_change',
                args=[write_batch.pk],
            ),
            'seller_url': reverse(
                'admin:catalog_sellerprofile_change',
                args=[seller.pk],
            ),
            'download_url': reverse(
                'admin:catalog_sellerprofile_wholesale_download',
                args=[seller.pk],
            ),
            'upload_url': reverse(
                'admin:catalog_sellerprofile_wholesale_update',
                args=[seller.pk],
            ),
        }
        return render(
            request,
            'admin/catalog/sellerprofile/wholesale_update_success.html',
            context,
        )

    def photo_import_view(self, request, object_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        seller = self._seller_or_404(object_id)
        form = PhotoImportUploadForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            uploaded = form.cleaned_data['file']
            payload = uploaded.read()
            filename = photo_zip_filename(getattr(uploaded, 'name', ''))
            try:
                rows = plan_product_photo_import(seller, payload)
            except PhotoImportError as exc:
                form.add_error('file', str(exc))
            else:
                file_hash = photo_sha256_bytes(payload)
                archive_path = persist_photo_zip(seller, payload, file_hash)
                batch, _summary = persist_photo_import_batch(
                    seller=seller,
                    rows=rows,
                    filename=filename,
                    file_sha256=file_hash,
                    mode=CatalogImportBatch.MODE_DRY_RUN,
                    source_archive_path=archive_path,
                    uploaded_by=request.user,
                )
                return redirect(
                    'admin:catalog_sellerprofile_photo_preview',
                    object_id,
                    batch.pk,
                )
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'form': form,
            'title': f'Загрузить фотографии товаров ZIP: {seller.name}',
        }
        return render(
            request,
            'admin/catalog/sellerprofile/photo_import.html',
            context,
        )

    def photo_preview_view(self, request, object_id, batch_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        seller = self._seller_or_404(object_id)
        batch = get_object_or_404(
            CatalogImportBatch,
            pk=batch_id,
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_PRODUCT_PHOTOS,
            mode=CatalogImportBatch.MODE_DRY_RUN,
        )
        rows = preview_photo_display_rows(batch)
        summary = {
            'rows': batch.source_row_count,
            'matched': batch.selected_count,
            'updated': batch.updated_count,
            'unchanged': batch.unchanged_count,
            'skipped': batch.skipped_count,
            'missing': batch.missing_from_source_count,
            'duplicates': batch.conflict_count,
            'errors': batch.error_count,
            'has_blockers': bool(batch.conflict_count or batch.error_count),
        }
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'batch': batch,
            'rows': rows,
            'summary': summary,
            'title': f'Предпросмотр фотографий: {seller.name}',
            'apply_url': reverse(
                'admin:catalog_sellerprofile_photo_apply',
                args=[seller.pk, batch.pk],
            ),
            'upload_url': reverse(
                'admin:catalog_sellerprofile_photo_import',
                args=[seller.pk],
            ),
        }
        return render(
            request,
            'admin/catalog/sellerprofile/photo_import_preview.html',
            context,
        )

    def photo_apply_view(self, request, object_id, batch_id):
        if not self._can_wholesale_update(request):
            return HttpResponseForbidden('Недостаточно прав.')
        if request.method != 'POST':
            return redirect(
                'admin:catalog_sellerprofile_photo_preview',
                object_id,
                batch_id,
            )
        seller = self._seller_or_404(object_id)
        batch = get_object_or_404(
            CatalogImportBatch,
            pk=batch_id,
            seller_profile=seller,
            source=CatalogImportBatch.SOURCE_PRODUCT_PHOTOS,
            mode=CatalogImportBatch.MODE_DRY_RUN,
        )
        if request.POST.get('confirm') != '1':
            messages.error(request, 'Подтвердите применение фотографий.')
            return redirect(
                'admin:catalog_sellerprofile_photo_preview',
                seller.pk,
                batch.pk,
            )
        try:
            write_batch, summary = apply_photo_import_preview(
                seller=seller,
                preview_batch=batch,
                applied_by=request.user,
            )
        except PhotoImportStaleError:
            messages.error(request, PHOTO_STALE_APPLY_MESSAGE)
            return redirect(
                'admin:catalog_sellerprofile_photo_import',
                seller.pk,
            )
        except PhotoImportBlockedError as exc:
            messages.error(request, str(exc))
            return redirect(
                'admin:catalog_sellerprofile_photo_preview',
                seller.pk,
                batch.pk,
            )
        except PhotoImportError as exc:
            messages.error(request, str(exc))
            return redirect(
                'admin:catalog_sellerprofile_photo_preview',
                seller.pk,
                batch.pk,
            )
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': seller,
            'seller': seller,
            'batch': write_batch,
            'summary': summary,
            'title': 'Фотографии применены',
            'history_url': reverse(
                'admin:catalog_catalogimportbatch_change',
                args=[write_batch.pk],
            ),
            'seller_url': reverse(
                'admin:catalog_sellerprofile_change',
                args=[seller.pk],
            ),
            'upload_url': reverse(
                'admin:catalog_sellerprofile_photo_import',
                args=[seller.pk],
            ),
        }
        return render(
            request,
            'admin/catalog/sellerprofile/photo_import_success.html',
            context,
        )


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
        'public_url',
        'is_active',
        'publish_to_kaspi',
        'last_known_our_price',
        'last_known_kaspi_qty',
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
        'last_known_kaspi_qty',
        'last_synced_at',
        'kaspi_open_link',
    )
    search_fields = (
        'master_sku',
        'merchant_sku',
        'barcode',
        'public_url',
        'product__article',
        'product__title',
    )
    list_filter = ('is_active', 'publish_to_kaspi')
    autocomplete_fields = ('product',)
    readonly_fields = ('created_at', 'updated_at', 'kaspi_open_link')
    fields = (
        'product',
        'master_sku',
        'merchant_sku',
        'barcode',
        'public_url',
        'kaspi_open_link',
        'is_active',
        'publish_to_kaspi',
        'last_known_our_price',
        'last_known_kaspi_qty',
        'last_synced_at',
        'created_at',
        'updated_at',
    )

    @admin.display(description='Открыть Kaspi')
    def kaspi_open_link(self, obj):
        url = (obj.public_url or '').strip()
        if not url:
            return '—'
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">Открыть Kaspi</a>',
            url,
        )


class CatalogImportItemInline(admin.TabularInline):
    model = CatalogImportItem
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ('article', 'action', 'product', 'warnings', 'errors', 'changed_fields')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    ordering = ('code',)


@admin.register(ProductWarehouseStock)
class ProductWarehouseStockAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'product_article',
        'warehouse',
        'quantity',
        'updated_at',
    )
    list_filter = ('warehouse',)
    search_fields = ('product__article', 'product__title', 'warehouse__code')
    autocomplete_fields = ('product', 'warehouse')
    readonly_fields = ('updated_at',)

    @admin.display(description='Артикул', ordering='product__article')
    def product_article(self, obj):
        return obj.product.article

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj:
            fields.extend(['product', 'warehouse'])
        return fields

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        set_stock_quantity(
            product=obj.product,
            warehouse=obj.warehouse,
            new_quantity=form.cleaned_data.get('quantity', obj.quantity),
            source='admin',
            note=f'user:{request.user.pk}',
        )
        stock = ProductWarehouseStock.objects.filter(
            product=obj.product,
            warehouse=obj.warehouse,
        ).first()
        if stock is None:
            return
        obj.pk = stock.pk
        obj.quantity = stock.quantity
        obj.updated_at = stock.updated_at


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'product',
        'product_article',
        'warehouse',
        'movement_type',
        'quantity_delta',
        'quantity_before',
        'quantity_after',
        'source',
        'reference',
    )
    list_filter = ('warehouse', 'movement_type', 'created_at')
    search_fields = (
        'product__article',
        'product__title',
        'reference',
        'source',
        'note',
    )
    autocomplete_fields = ('product', 'warehouse')
    readonly_fields = (
        'product',
        'warehouse',
        'movement_type',
        'quantity_delta',
        'quantity_before',
        'quantity_after',
        'source',
        'reference',
        'note',
        'created_at',
    )
    ordering = ('-created_at', '-id')

    @admin.display(description='Артикул', ordering='product__article')
    def product_article(self, obj):
        return obj.product.article

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KaspiListingFactSnapshot)
class KaspiListingFactSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'listing',
        'observed_price',
        'observed_qty',
        'source',
        'source_filename',
        'source_row',
        'observed_at',
        'import_batch',
    )
    list_filter = ('source', 'observed_at')
    search_fields = (
        'listing__master_sku',
        'listing__product__article',
        'source_filename',
        'source_sha256',
    )
    autocomplete_fields = ('listing', 'import_batch')
    readonly_fields = (
        'listing',
        'observed_price',
        'observed_qty',
        'source',
        'source_filename',
        'source_sha256',
        'source_row',
        'observed_at',
        'import_batch',
        'created_at',
    )
    ordering = ('-observed_at', '-id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KaspiOrder)
class KaspiOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'seller_profile',
        'external_order_id',
        'first_operation_at',
        'last_operation_at',
        'updated_at',
    )
    list_filter = ('seller_profile',)
    search_fields = ('external_order_id',)
    autocomplete_fields = ('seller_profile',)
    readonly_fields = (
        'seller_profile',
        'external_order_id',
        'first_operation_at',
        'last_operation_at',
        'created_at',
        'updated_at',
    )
    ordering = ('-last_operation_at', '-id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KaspiSalesOperation)
class KaspiSalesOperationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'operation_at',
        'operation_type',
        'order',
        'product',
        'listing',
        'quantity',
        'gross_amount',
        'commission_amount',
        'delivery_cost',
        'match_status',
    )
    list_filter = ('operation_type', 'match_status', 'seller_profile')
    search_fields = (
        'order__external_order_id',
        'product__article',
        'details',
        'source_fingerprint',
        'matched_identifier',
    )
    autocomplete_fields = (
        'seller_profile',
        'order',
        'product',
        'listing',
        'import_batch',
    )
    readonly_fields = (
        'seller_profile',
        'order',
        'product',
        'listing',
        'purchase_return_document',
        'sales_point_id',
        'terminal_id',
        'operation_type',
        'operation_at',
        'accounting_date',
        'payment_type',
        'payment_type_2',
        'gross_amount',
        'settlement_amount',
        'commission_amount',
        'commission_ex_vat_amount',
        'commission_ex_vat_percent',
        'card_commission_amount',
        'card_commission_percent',
        'payment_guarantee_amount',
        'payment_guarantee_percent',
        'kaspi_pay_commission_amount',
        'kaspi_pay_commission_percent',
        'kaspi_travel_commission_amount',
        'kaspi_travel_commission_percent',
        'bonus_product_amount',
        'bonus_review_amount',
        'delivery_document',
        'delivery_cost',
        'installment_term',
        'details',
        'quantity',
        'unit_gross_amount',
        'match_status',
        'matched_identifier',
        'source_filename',
        'source_sha256',
        'source_row',
        'source_fingerprint',
        'import_batch',
        'raw_data',
        'created_at',
    )
    ordering = ('-operation_at', '-id')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KaspiEconomicsConfig)
class KaspiEconomicsConfigAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'seller_profile',
        'fulfillment_packaging_per_unit',
        'fulfillment_handling_per_unit',
        'fulfillment_total_display',
        'default_min_margin_percent',
        'is_active',
        'updated_at',
    )
    list_filter = ('is_active',)
    search_fields = ('seller_profile__name',)
    autocomplete_fields = ('seller_profile',)
    readonly_fields = ('fulfillment_total_display', 'created_at', 'updated_at')

    @admin.display(description='Fulfillment всего / ед.')
    def fulfillment_total_display(self, obj):
        return obj.fulfillment_total_per_unit


@admin.register(ProductKaspiEconomicsPolicy)
class ProductKaspiEconomicsPolicyAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'article',
        'min_margin_percent',
        'manual_min_price',
        'updated_at',
    )
    search_fields = ('product__article', 'product__title')
    autocomplete_fields = ('product',)
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='Артикул')
    def article(self, obj):
        return obj.product.article


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
        'uploaded_by',
        'applied_by',
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
        'uploaded_by',
        'applied_by',
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


class MaintenanceKitItemInline(admin.TabularInline):
    model = MaintenanceKitItem
    extra = 0
    autocomplete_fields = ('product',)
    fields = ('product', 'quantity')


@admin.register(MaintenanceKit)
class MaintenanceKitAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'slug',
        'brand',
        'car_model',
        'engine',
        'is_active',
        'has_cover',
        'base_price_display',
        'available_kits_display',
    )
    list_filter = ('is_active', 'brand', 'car_model')
    search_fields = ('name', 'slug', 'engine', 'description')
    autocomplete_fields = ('brand', 'car_model')
    readonly_fields = (
        'cover_preview',
        'base_price_display',
        'available_kits_display',
        'created_at',
        'updated_at',
    )
    inlines = (MaintenanceKitItemInline,)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'name',
                    'slug',
                    'brand',
                    'car_model',
                    'engine',
                    'year_from',
                    'year_to',
                    'description',
                    'cover',
                    'cover_preview',
                    'is_active',
                    'base_price_display',
                    'available_kits_display',
                ),
            },
        ),
        (
            'Служебное',
            {'fields': ('created_at', 'updated_at')},
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('brand', 'car_model')
            .prefetch_related('items__product')
        )

    @admin.display(description='Базовая стоимость')
    def base_price_display(self, obj):
        from catalog.maintenance_kits import guest_kit_base_price

        if not obj.pk:
            return '—'
        price = guest_kit_base_price(obj)
        if price is None:
            return 'не рассчитана'
        return f'{price} ₸'

    @admin.display(description='Доступно комплектов')
    def available_kits_display(self, obj):
        from catalog.maintenance_kits import available_kits

        if not obj.pk:
            return '—'
        qty = available_kits(obj)
        if qty is None:
            return 'Наличие уточняется'
        return qty

    @admin.display(description='Общее фото', boolean=True)
    def has_cover(self, obj):
        return bool(obj.cover)

    @admin.display(description='Просмотр общего фото')
    def cover_preview(self, obj):
        if not obj.cover:
            return 'Нет общего фото — в витрине будет заготовка «Комплект ТО».'
        return format_html(
            '<img src="{}" alt="Общее фото комплекта" '
            'style="max-width:320px;max-height:240px;object-fit:contain;'
            'background:#f3f4f6;padding:8px;border-radius:8px;">',
            obj.cover.url,
        )

    def save_model(self, request, obj, form, change):
        from django.core.files.uploadedfile import UploadedFile

        from catalog.image_upload_policy import optimize_uploaded_image

        previous_name = ''
        if change and obj.pk:
            previous_name = (
                MaintenanceKit.objects.filter(pk=obj.pk)
                .values_list('cover', flat=True)
                .first()
                or ''
            )
        uploaded = form.cleaned_data.get('cover') if form is not None else None
        if isinstance(uploaded, UploadedFile):
            obj.cover = optimize_uploaded_image(uploaded)
        super().save_model(request, obj, form, change)
        new_name = getattr(obj.cover, 'name', '') or ''
        if previous_name and previous_name != new_name:
            storage = obj.cover.storage if obj.cover else None
            if storage is None:
                from django.core.files.storage import default_storage
                storage = default_storage
            if storage.exists(previous_name):
                storage.delete(previous_name)


@admin.register(MaintenanceKitItem)
class MaintenanceKitItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'kit', 'product', 'quantity')
    search_fields = ('kit__name', 'kit__slug', 'product__title', 'product__article')
    autocomplete_fields = ('kit', 'product')

