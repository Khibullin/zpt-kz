from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from service_requests.views import (
    service_request_result,
    services_catalog,
    service_seller_detail,
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # API
    path('api/', include('core.urls')),  # запчасти
    path('api/service/', include('service_requests.urls')),  # СТО / детейлинг

    # frontend service-request
    path(
        'service-request/',
        TemplateView.as_view(
            template_name='service-request/index.html'
        )
    ),

    path(
        'service-request/cabinet/',
        TemplateView.as_view(
            template_name='service-request/cabinet/index.html'
        )
    ),

    path(
        'service-request/result/<int:request_id>/',
        service_request_result,
        name='service_request_result_page',
    ),

    path(
        'service-request/register/',
        TemplateView.as_view(
            template_name='service-request/register/index.html'
        )
    ),

    path(
        'catalog/services/',
        services_catalog,
        name='services_catalog',
    ),

    path(
        'catalog/services/<int:seller_id>/',
        service_seller_detail,
        name='service_seller_detail',
    ),
]
