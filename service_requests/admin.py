from django.contrib import admin

from .models import (
    Service,
    ServiceSeller,
    ServiceRequest,
    ServiceMatch
)


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
    )

    list_filter = (
        'seller_type',
        'city',
        'is_active',
    )

    search_fields = (
        'name',
        'whatsapp',
        'city',
        'district',
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