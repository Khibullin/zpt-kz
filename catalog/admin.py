from django.contrib import admin
from .models import (
    Country,
    Brand,
    CarModel,
    Category,
    Product,
    SellerProfile,
    ProductImage,
)


admin.site.site_header = 'Администрирование ZPT Market'
admin.site.site_title = 'ZPT Market'
admin.site.index_title = 'Панель управления маркетом'


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'country',
    )

    list_filter = ('country',)

    search_fields = (
        'name',
        'country__name',
    )

    ordering = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'brand',
        'get_country',
    )

    list_filter = (
        'brand__country',
        'brand',
    )

    search_fields = (
        'name',
        'brand__name',
        'brand__country__name',
    )

    ordering = ('name',)

    def get_country(self, obj):
        return obj.brand.country.name
    get_country.short_description = 'Страна'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
    )

    search_fields = ('name',)
    ordering = ('name',)


@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'phone',
        'city',
        'user',
    )

    search_fields = (
        'name',
        'phone',
        'city',
        'user__username',
    )

    list_filter = ('city',)

    ordering = ('name',)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'product',
        'image',
    )

    search_fields = (
        'product__title',
        'product__article',
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'article',
        'price',
        'price_on_request',
        'seller_name',
        'whatsapp_number',
        'city',
        'brand',
        'car_model',
        'category',
        'condition',
        'status',
        'created_at',
    )

    list_filter = (
        'status',
        'condition',
        'price_on_request',
        'brand__country',
        'brand',
        'car_model',
        'category',
        'city',
    )

    search_fields = (
        'title',
        'article',
        'seller_name',
        'whatsapp_number',
        'description',
        'compatibility',
    )

    readonly_fields = (
        'created_at',
    )

    ordering = (
        '-created_at',
    )

    fieldsets = (
        (
            'Основная информация',
            {
                'fields': (
                    'title',
                    'article',
                    'category',
                    'price',
                    'price_on_request',
                    'condition',
                    'status',
                )
            }
        ),

        (
            'Автомобиль',
            {
                'fields': (
                    'brand',
                    'car_model',
                    'selected_models',
                    'compatibility',
                )
            }
        ),

        (
            'Продавец',
            {
                'fields': (
                    'seller_name',
                    'whatsapp_number',
                    'city',
                )
            }
        ),

        (
            'Описание и фото',
            {
                'fields': (
                    'description',
                    'main_image',
                )
            }
        ),

        (
            'Системная информация',
            {
                'fields': (
                    'created_at',
                )
            }
        ),
    )