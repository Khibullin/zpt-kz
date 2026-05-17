from django.urls import path
from .views import *

urlpatterns = [

    path(
        'create-service-seller/',
        create_service_seller,
        name='create_service_seller'
    ),

    path(
        'service-seller-login/',
        service_seller_login,
        name='service_seller_login'
    ),

    path(
        'create-service-request/',
        create_service_request,
        name='create_service_request'
    ),

    path(
        'service-requests/',
        get_service_requests,
        name='get_service_requests'
    ),

    path(
        'service-seller-profile/',
        get_service_seller_profile,
        name='get_service_seller_profile'
    ),

    path(
        'update-service-seller-profile/',
        update_service_seller_profile,
        name='update_service_seller_profile'
    ),

    path(
        'update-service-match-status/',
        update_service_match_status,
        name='update_service_match_status'
    ),

    path(
        'result/<int:request_id>/',
        service_request_result,
        name='service_request_result'
    ),

    path(
        'catalog/services/<int:seller_id>/',
        service_seller_detail,
        name='service_seller_detail'
    ),

]