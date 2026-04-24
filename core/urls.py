from django.urls import path
from .views import (
    create_request,
    create_seller,
    countries_list,
    brands_by_country,
    models_by_brand,
    seller_requests,
)

urlpatterns = [
    path('create-request/', create_request, name='create_request'),
    path('create-seller/', create_seller, name='create_seller'),
    path('countries/', countries_list, name='countries_list'),
    path('brands-by-country/', brands_by_country, name='brands_by_country'),
    path('models-by-brand/', models_by_brand, name='models_by_brand'),
    path('seller-requests/', seller_requests, name='seller_requests'),
]