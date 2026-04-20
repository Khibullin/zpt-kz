from django.urls import path
from .views import create_request, countries_list, brands_by_country, models_by_brand

urlpatterns = [
    path('create-request/', create_request, name='create_request'),
    path('countries/', countries_list, name='countries_list'),
    path('brands-by-country/', brands_by_country, name='brands_by_country'),
    path('models-by-brand/', models_by_brand, name='models_by_brand'),
]