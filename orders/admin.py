from django.contrib import admin
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.html import format_html

from catalog.models import SellerProfile
from catalog.wholesale import format_wholesale_terms_admin_text

from .models import CartItem, KaspiTransaction, Order, OrderItem, WholesaleFunnelEvent
from .wholesale_analytics import (
    build_wholesale_funnel_report,
    default_report_dates,
    format_conversion,
    parse_report_date,
)


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
    change_list_template = 'admin/orders/order/change_list.html'
    list_display = (
        'id',
        'created_at',
        'order_type',
        'seller_name',
        'customer_name',
        'customer_phone',
        'status',
        'total_price',
        'delivery_method',
    )
    list_filter = ('order_type', 'status', 'seller_name', 'delivery_method', 'created_at')
    search_fields = (
        'id',
        'seller_name',
        'seller_whatsapp',
        'customer_name',
        'customer_phone',
        'utm_source',
        'utm_campaign',
        'items__product__title',
        'items__product__article',
    )
    readonly_fields = (
        'created_at',
        'updated_at',
        'access_token',
        'seller_name',
        'seller_whatsapp',
        'order_type',
        'utm_source',
        'utm_medium',
        'utm_campaign',
        'wholesale_terms_snapshot_display',
    )
    exclude = ('wholesale_terms_snapshot',)
    inlines = [OrderItemInline, KaspiTransactionInline]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['wholesale_funnel_url'] = reverse(
            'admin:orders_wholesalefunnelevent_funnel'
        )
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description='Оптовые условия (снимок заказа)')
    def wholesale_terms_snapshot_display(self, obj):
        if not obj.is_wholesale:
            return '—'
        text = format_wholesale_terms_admin_text(obj.wholesale_terms_snapshot or {})
        return format_html('<pre style="white-space:pre-wrap;margin:0;">{}</pre>', text)


@admin.register(KaspiTransaction)
class KaspiTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'kaspi_id', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('kaspi_id', 'order__id')


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'product', 'quantity', 'updated_at')
    search_fields = ('user__username', 'product__title', 'product__article')


@admin.register(WholesaleFunnelEvent)
class WholesaleFunnelEventAdmin(admin.ModelAdmin):
    change_list_template = 'admin/orders/wholesalefunnelevent/change_list.html'
    date_hierarchy = 'created_at'
    list_display = (
        'created_at',
        'event_type',
        'seller_profile',
        'product',
        'order',
        'utm_source',
        'utm_medium',
        'utm_campaign',
    )
    list_filter = (
        'event_type',
        'seller_profile',
        'utm_source',
        'utm_medium',
        'utm_campaign',
        'created_at',
    )
    search_fields = (
        'product__article',
        'product__title',
        'order__id',
        'utm_campaign',
        'visitor_id',
    )
    list_select_related = ('seller_profile', 'product', 'order')
    readonly_fields = (
        'event_type',
        'seller_profile',
        'product',
        'order',
        'visitor_id',
        'utm_source',
        'utm_medium',
        'utm_campaign',
        'quantity',
        'value_kzt',
        'metadata',
        'created_at',
    )
    ordering = ('-created_at',)
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        user = getattr(request, 'user', None)
        if not user or not user.is_staff:
            return False
        if user.is_superuser:
            return True
        return user.has_perm('orders.view_wholesalefunnelevent') or user.has_perm(
            'orders.view_order'
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['wholesale_funnel_url'] = reverse(
            'admin:orders_wholesalefunnelevent_funnel'
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'funnel/',
                self.admin_site.admin_view(self.funnel_report_view),
                name='orders_wholesalefunnelevent_funnel',
            ),
        ]
        return custom + urls

    def funnel_report_view(self, request):
        if not self.has_view_permission(request):
            return HttpResponseForbidden('Недостаточно прав.')

        default_from, default_to = default_report_dates()
        date_from = parse_report_date(request.GET.get('date_from'), default_from)
        date_to = parse_report_date(request.GET.get('date_to'), default_to)
        if date_from > date_to:
            date_from, date_to = date_to, date_from

        seller_id = None
        seller_raw = (request.GET.get('seller') or '').strip()
        if seller_raw.isdigit():
            seller_id = int(seller_raw)

        utm_source = (request.GET.get('utm_source') or '').strip()
        utm_medium = (request.GET.get('utm_medium') or '').strip()
        utm_campaign = (request.GET.get('utm_campaign') or '').strip()

        report = build_wholesale_funnel_report(
            date_from,
            date_to,
            seller_id=seller_id,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
        )
        conversions = {
            key: format_conversion(value)
            for key, value in report['conversions'].items()
        }
        campaigns = []
        for row in report['campaigns']:
            item = dict(row)
            item['conversion_display'] = format_conversion(row['conversion'])
            campaigns.append(item)

        recent_orders = []
        for event in report['recent_orders']:
            order = event.order
            if order is None or order.order_type != Order.ORDER_TYPE_WHOLESALE:
                continue
            recent_orders.append({
                'order': order,
                'admin_url': reverse('admin:orders_order_change', args=[order.pk]),
                'seller_name': event.seller_profile.name if event.seller_profile else order.seller_name,
                'quantity': order.total_quantity,
                'total_price': order.total_price,
                'utm_source': order.utm_source,
                'utm_medium': order.utm_medium,
                'utm_campaign': order.utm_campaign,
            })

        context = {
            **self.admin_site.each_context(request),
            'title': 'Оптовая аналитика',
            'opts': self.model._meta,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'seller_id': seller_id or '',
            'utm_source': utm_source,
            'utm_medium': utm_medium,
            'utm_campaign': utm_campaign,
            'sellers': SellerProfile.objects.order_by('name'),
            'report': report,
            'conversions': conversions,
            'campaigns': campaigns,
            'recent_orders': recent_orders,
            'events_changelist_url': reverse(
                'admin:orders_wholesalefunnelevent_changelist'
            ),
        }
        return render(
            request,
            'admin/orders/wholesalefunnelevent/funnel_report.html',
            context,
        )
