from django.contrib import admin

from .models import (
Country,
Brand,
CarModel,
PartCategory,
Request,
Seller,
Match
)


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display=('id','name')
    search_fields=('name',)



@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display=(
      'id',
      'name',
      'country',
      'transport_type'
    )

    list_filter=(
      'country',
      'transport_type'
    )

    search_fields=('name',)



@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):

    list_display=(
      'id',
      'name',
      'brand',
      'transport_type'
    )

    list_filter=(
      'transport_type',
      'brand',
      'brand__country'
    )

    search_fields=('name',)



@admin.register(PartCategory)
class PartCategoryAdmin(admin.ModelAdmin):

    list_display=(
      'id',
      'name'
    )

    search_fields=('name',)



@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):

    list_display=(
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

    list_filter=(
       'transport_type',
       'status',
       'city',
       'category'
    )

    search_fields=(
      'country',
      'brand',
      'model',
      'article',
      'description',
      'phone'
    )



@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):

    list_display=(
      'id',
      'name',
      'whatsapp',
      'transport_type',

      'all_categories',
      'all_brands',
      'all_models',
      'all_countries',

      'city',
      'is_active',
      'is_paused'
    )

    list_filter=(
      'transport_type',
      'is_active',
      'is_paused',
      'all_categories',
      'all_brands',
      'all_models',
      'all_countries',
      'city'
    )

    search_fields=(
      'name',
      'whatsapp',
      'brand',
      'model'
    )

    filter_horizontal=(
      'selected_categories',
      'selected_countries',
      'selected_brands',
      'selected_models'
    )

    fieldsets=(

      ('Основное', {
        'fields':(
          'name',
          'whatsapp',
          'transport_type',
          'city',
          'is_active',
          'is_paused'
        )
      }),

      ('Старый одиночный режим', {
        'fields':(
          'category',
          'country_fk',
          'brand_fk',
          'model_fk'
        )
      }),

      ('Новый множественный выбор', {
        'fields':(
          'selected_categories',
          'selected_countries',
          'selected_brands',
          'selected_models'
        )
      }),

      ('Режимы "Все"', {
        'fields':(
          'all_categories',
          'all_countries',
          'all_brands',
          'all_models'
        )
      }),

    )



@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):

    list_display=(
      'id',
      'request',
      'seller',
      'status',
      'created_at',
      'sent_at'
    )

    list_filter=('status',)

    search_fields=(
      'request__phone',
      'seller__name'
    )