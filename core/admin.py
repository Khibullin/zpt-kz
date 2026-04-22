from django.contrib import admin
from .models import Country, Brand, CarModel, Request, Seller, Match


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'country', 'transport_type')
    list_filter = ('country', 'transport_type')
    search_fields = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'brand', 'transport_type')
    list_filter = ('transport_type', 'brand', 'brand__country')
    search_fields = ('name',)


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'transport_type', 'country', 'brand', 'model',
        'category', 'city', 'phone', 'status', 'created_at'
    )
    list_filter = ('transport_type', 'status', 'city', 'category')
    search_fields = ('country', 'brand', 'model', 'article', 'description', 'phone')


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'whatsapp', 'transport_type',
        'all_countries', 'country_fk',
        'all_brands', 'brand_fk',
        'all_models', 'model_fk',
        'all_categories', 'category',
        'city', 'is_active', 'is_paused'
    )
    list_filter = (
        'transport_type', 'is_active', 'is_paused',
        'all_countries', 'all_brands', 'all_models', 'all_categories',
        'city', 'category', 'country_fk', 'brand_fk'
    )
    search_fields = (
        'name', 'whatsapp', 'brand', 'model',
        'brand_fk__name', 'model_fk__name', 'country_fk__name'
    )


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'request', 'seller', 'status', 'created_at', 'sent_at')
    list_filter = ('status',)
    search_fields = ('request__phone', 'seller__name')