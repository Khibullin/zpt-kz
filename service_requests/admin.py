from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ServiceBroadcastSettings,
    Service,
    ServiceSeller,
    ServiceRequest,
    ServiceMatch,
    ServiceWhatsAppMessageLog,
)

@admin.register(ServiceBroadcastSettings)
class ServiceBroadcastSettingsAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'mode',
        'updated_at',
    )

    readonly_fields = (
        'updated_at',
    )

    def has_add_permission(self, request):
        if ServiceBroadcastSettings.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'name',
    )

    search_fields = (
        'name',
    )


@admin.register(ServiceSeller)
class ServiceSellerAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'name',
        'whatsapp',
        'seller_type',
        'city',
        'district',
        'is_active',
        'receive_requests',
        'is_test_seller',
        'is_paused',
        'dispatch_priority',
    )

    list_editable = (
        'receive_requests',
        'is_test_seller',
        'is_paused',
        'dispatch_priority',
    )

    list_filter = (
        'seller_type',
        'city',
        'is_active',
        'receive_requests',
        'is_test_seller',
        'is_paused',
    )

    search_fields = (
        'name',
        'whatsapp',
        'city',
        'district',
    )

    fieldsets = (
        ('Основное', {
            'fields': (
                'name',
                'whatsapp',
                'password',
                'seller_type',
                'city',
                'district',
                'address',
                'map_link',
                'services',
            )
        }),

        ('Статусы и рассылка', {
            'fields': (
                'is_active',
                'receive_requests',
                'is_test_seller',
                'is_paused',
                'dispatch_priority',
            )
        }),
    )

    filter_horizontal = (
        'services',
    )


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'service_type',
        'city',
        'phone',
        'created_at',
    )

    list_filter = (
        'service_type',
        'city',
    )

    search_fields = (
        'phone',
        'description',
        'city',
    )

    filter_horizontal = (
        'services',
    )


@admin.register(ServiceMatch)
class ServiceMatchAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'request',
        'seller',
        'status',
        'created_at',
    )

    list_filter = (
        'status',
    )

    search_fields = (
        'seller__name',
        'request__phone',
    )

# ================================
# WhatsApp логи исполнителей
# ================================

@admin.register(ServiceWhatsAppMessageLog)
class ServiceWhatsAppMessageLogAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'created_at',
        'seller',
        'phone',
        'message_type',
        'status',
        'meta_message_id',
    )

    list_filter = (
        'status',
        'message_type',
        'created_at',
    )

    search_fields = (
        'phone',
        'seller__name',
        'seller__whatsapp',
        'meta_message_id',
        'error_text',
    )

