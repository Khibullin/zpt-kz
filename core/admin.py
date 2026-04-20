from django.contrib import admin
from .models import Country, Brand, CarModel, Request, Seller, Match


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'country')
    list_filter = ('country',)
    search_fields = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'brand')
    list_filter = ('brand', 'brand__country')
    search_fields = ('name',)


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'transport_type', 'brand', 'model', 'category', 'city', 'phone', 'status', 'created_at')
    list_filter = ('transport_type', 'status', 'city', 'category')
    search_fields = ('brand', 'model', 'article', 'description', 'phone')


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'whatsapp', 'transport_type', 'city', 'category', 'brand', 'model', 'is_active', 'is_paused')
    list_filter = ('transport_type', 'is_active', 'is_paused', 'city', 'category')
    search_fields = ('name', 'whatsapp', 'brand', 'model')


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'request', 'seller', 'status', 'created_at', 'sent_at')
    list_filter = ('status',)
    search_fields = ('request__phone', 'seller__name')