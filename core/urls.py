from django.urls import path

from .views import (
    create_request,
    create_seller,
    seller_login,
    seller_logout,
    change_seller_password,
    countries_list,
    brands_by_country,
    models_by_brand,
    part_categories_list,
    seller_requests,
    seller_profile,
    toggle_seller_pause,
    update_seller_profile,
    update_match_status,
    parts_sellers_catalog,
    parts_seller_detail,
    view_request_status,
)


urlpatterns = [
    path('create-request/', create_request, name='create_request'),
    path('my-request/<int:req_id>/', view_request_status, name='view_request_status'),
    path('create-seller/', create_seller, name='create_seller'),

    path('seller-login/', seller_login, name='api_seller_login'),
    path('seller-logout/', seller_logout, name='seller_logout'),
    path('change-seller-password/', change_seller_password, name='change_seller_password'),

    path('countries/', countries_list, name='countries_list'),
    path('brands-by-country/', brands_by_country, name='brands_by_country'),
    path('models-by-brand/', models_by_brand, name='models_by_brand'),
    path('part-categories/', part_categories_list, name='part_categories_list'),

    path('seller-requests/', seller_requests, name='seller_requests'),
    path('seller-profile/', seller_profile, name='seller_profile'),
    path('toggle-seller-pause/', toggle_seller_pause, name='toggle_seller_pause'),
    path('update-seller-profile/', update_seller_profile, name='update_seller_profile'),
    path('update-match-status/', update_match_status, name='update_match_status'),

    path(
        'parts-sellers/',
        parts_sellers_catalog,
        name='parts_sellers_catalog'
    ),

    path(
        'parts-seller/<int:seller_id>/',
        parts_seller_detail,
        name='parts_seller_detail'
    ),
]