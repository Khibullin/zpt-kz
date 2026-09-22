from django.contrib import admin

from .admin_views import _active_superuser
from .models import PaymentAttempt


class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = (
        'inv_id',
        'order_id',
        'amount',
        'currency',
        'mode',
        'status',
        'seller_profile_id',
        'created_by',
        'created_at',
        'confirmed_at',
    )
    list_filter = ('status', 'mode', 'created_at')
    search_fields = ('inv_id', 'order__id', 'merchant_login')
    readonly_fields = (
        'inv_id',
        'order',
        'amount',
        'currency',
        'mode',
        'hash_algo',
        'merchant_login',
        'seller_profile_id',
        'created_by',
        'status',
        'created_at',
        'confirmed_at',
    )
    ordering = ('-created_at',)
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _active_superuser(request)


admin.site.register(PaymentAttempt, PaymentAttemptAdmin)
