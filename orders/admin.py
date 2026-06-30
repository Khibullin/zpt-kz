from django.contrib import admin

from .models import Order, OrderItem, KaspiTransaction


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
        'customer_name',
        'customer_phone',
        'status',
        'total_price',
        'delivery_method',
        'created_at',
    )
    list_filter = ('status', 'delivery_method', 'created_at')
    search_fields = ('customer_name', 'customer_phone', 'id')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [OrderItemInline, KaspiTransactionInline]


@admin.register(KaspiTransaction)
class KaspiTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'kaspi_id', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('kaspi_id', 'order__id')
