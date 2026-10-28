from django.contrib import admin
from .models import Membership, Supplier, Offer, Draft, Activity

@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'active']
    list_filter = ['role', 'active']
    autocomplete_fields = ['user']

@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ['article', 'brand', 'name', 'supplier', 'cost', 'checked_on']
    search_fields = ['article', 'brand', 'name']
    list_filter = ['supplier', 'match_verified']

admin.site.register(Supplier)
admin.site.register(Draft)

@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'created_at']
    readonly_fields = ['user', 'action', 'details', 'created_at']
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
