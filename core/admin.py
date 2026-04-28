from django.contrib import admin
from django.contrib import messages

from .models import (
    Country,
    Brand,
    CarModel,
    PartCategory,
    BroadcastSettings,
    Request,
    Seller,
    Match,
    RequestDispatch,
)


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'country',
        'transport_type'
    )

    list_filter = (
        'country',
        'transport_type'
    )

    search_fields = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'brand',
        'transport_type'
    )

    list_filter = (
        'transport_type',
        'brand',
        'brand__country'
    )

    search_fields = ('name',)


@admin.register(PartCategory)
class PartCategoryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name'
    )

    search_fields = ('name',)


@admin.register(BroadcastSettings)
class BroadcastSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'mode',
        'wave_size',
        'wave_interval_minutes',
        'emergency_stop',
        'updated_at',
    )

    readonly_fields = (
        'updated_at',
    )

    fieldsets = (
        ('Broadcast Control', {
            'fields': (
                'mode',
                'wave_size',
                'wave_interval_minutes',
                'emergency_stop',
                'updated_at',
            )
        }),
    )

    def has_add_permission(self, request):
        if BroadcastSettings.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'transport_type',
        'country',
        'brand',
        'model',
        'category',
        'city',
        'phone',
        'status',
        'created_at'
    )

    list_filter = (
        'transport_type',
        'status',
        'city',
        'category'
    )

    search_fields = (
        'country',
        'brand',
        'model',
        'article',
        'description',
        'phone'
    )


@admin.action(description='Включить получение заявок')
def enable_receive_requests(modeladmin, request, queryset):
    updated = queryset.update(receive_requests=True)
    messages.success(request, f'Получение заявок включено: {updated} продавцов.')


@admin.action(description='Выключить получение заявок')
def disable_receive_requests(modeladmin, request, queryset):
    updated = queryset.update(receive_requests=False)
    messages.warning(request, f'Получение заявок выключено: {updated} продавцов.')


@admin.action(description='Пометить как тестовых продавцов')
def mark_as_test_seller(modeladmin, request, queryset):
    updated = queryset.update(is_test_seller=True)
    messages.success(request, f'Помечено как тестовые продавцы: {updated}.')


@admin.action(description='Снять признак тестовых продавцов')
def unmark_as_test_seller(modeladmin, request, queryset):
    updated = queryset.update(is_test_seller=False)
    messages.warning(request, f'Снят признак тестовых продавцов: {updated}.')


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'whatsapp',
        'transport_type',
        'dispatch_priority',

        'receive_requests',
        'is_test_seller',

        'all_categories',
        'all_brands',
        'all_models',
        'all_countries',

        'city',
        'is_active',
        'is_paused'
    )

    list_editable = (
        'dispatch_priority',
        'receive_requests',
        'is_test_seller',
    )

    list_filter = (
        'transport_type',
        'receive_requests',
        'is_test_seller',
        'is_active',
        'is_paused',
        'all_categories',
        'all_brands',
        'all_models',
        'all_countries',
        'city'
    )

    search_fields = (
        'name',
        'whatsapp',
        'brand',
        'model'
    )

    filter_horizontal = (
        'selected_categories',
        'selected_countries',
        'selected_brands',
        'selected_models'
    )

    actions = (
        enable_receive_requests,
        disable_receive_requests,
        mark_as_test_seller,
        unmark_as_test_seller,
    )

    fieldsets = (

        ('Основное', {
            'fields': (
                'name',
                'whatsapp',
                'transport_type',
                'city',
                'dispatch_priority',
                'is_active',
                'is_paused',
                'receive_requests',
                'is_test_seller',
            )
        }),

        ('Старый одиночный режим', {
            'fields': (
                'category',
                'country_fk',
                'brand_fk',
                'model_fk'
            )
        }),

        ('Новый множественный выбор', {
            'fields': (
                'selected_categories',
                'selected_countries',
                'selected_brands',
                'selected_models'
            )
        }),

        ('Режимы "Все"', {
            'fields': (
                'all_categories',
                'all_countries',
                'all_brands',
                'all_models'
            )
        }),

    )


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request',
        'seller',
        'status',
        'created_at',
        'sent_at'
    )

    list_filter = ('status',)

    search_fields = (
        'request__phone',
        'seller__name'
    )


@admin.action(description='Остановить выбранные волны')
def pause_dispatches(modeladmin, request, queryset):
    updated = queryset.exclude(status=RequestDispatch.STATUS_SENT).update(
        status=RequestDispatch.STATUS_PAUSED
    )
    messages.warning(request, f'Остановлено волн/отправок: {updated}.')


@admin.action(description='Вернуть выбранные волны в очередь')
def queue_dispatches(modeladmin, request, queryset):
    updated = queryset.exclude(status=RequestDispatch.STATUS_SENT).update(
        status=RequestDispatch.STATUS_QUEUED
    )
    messages.success(request, f'Возвращено в очередь: {updated}.')


@admin.register(RequestDispatch)
class RequestDispatchAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request',
        'seller',
        'wave_number',
        'position_number',
        'status',
        'scheduled_at',
        'sent_at',
        'created_at'
    )

    list_filter = (
        'status',
        'wave_number',
        'scheduled_at',
        'sent_at'
    )

    search_fields = (
        'request__phone',
        'request__brand',
        'request__model',
        'seller__name',
        'seller__whatsapp'
    )

    readonly_fields = (
        'created_at',
    )

    actions = (
        pause_dispatches,
        queue_dispatches,
    )