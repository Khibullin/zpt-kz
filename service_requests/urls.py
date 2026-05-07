from django.urls import path
from .views import *

urlpatterns = [
    path('create-service-seller/', create_service_seller),
    path('service-seller-login/', service_seller_login),

    path('service-seller-profile/', get_service_seller_profile),
    path('update-service-seller-profile/', update_service_seller_profile),

    path('create-service-request/', create_service_request),
    path('get-requests/', get_service_requests),
]