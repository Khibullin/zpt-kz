from django.contrib import admin

from .models import Order, OrderItem, KaspiTransaction, CartItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'quantity', 'price_at_purchase')


class KaspiTransactionInline(admin.TabularInline):
    model = KaspiTransaction
    extra = 0
    readonly_fields = ('kaspi_id', 'status', 'raw_response', 'created_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'seller_name',
        'customer_name',
        'customer_phone',
        'status',
        'total_price',
        'delivery_method',
    )
    list_filter = ('status', 'seller_name', 'delivery_method', 'created_at')
    search_fields = (
        'id',
        'seller_name',
        'seller_whatsapp',
        'customer_name',
        'customer_phone',
        'items__product__title',
        'items__product__article',
    )
    readonly_fields = (
        'created_at',
        'updated_at',
        'access_token',
        'seller_name',
        'seller_whatsapp',
    )
    inlines = [OrderItemInline, KaspiTransactionInline]


@admin.register(KaspiTransaction)
class KaspiTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'kaspi_id', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('kaspi_id', 'order__id')


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'product', 'quantity', 'updated_at')
    search_fields = ('user__username', 'product__title', 'product__article')
