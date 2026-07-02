from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

from backend.pwa_views import manifest_json, service_worker_js
from core.views import (
    parts_sellers_catalog,
    parts_seller_detail,
    view_request_status,
    seller_landing,
    register_seller,
    business_gateway,
)

from service_requests.views import (
    service_request_result,
    services_catalog,
    service_seller_detail,
)


urlpatterns = [
    path('admin/', admin.site.urls),

    path('', include('orders.urls')),

    path('prodavat/', seller_landing, name='seller_landing'),
    path('register/', register_seller, name='register_seller'),
    path('business/', business_gateway, name='business_gateway'),

    # PWA
    path('manifest.json', manifest_json, name='pwa_manifest'),
    path('service-worker.js', service_worker_js, name='pwa_service_worker'),

    # ZPT MARKET


    # API
    path('api/', include('core.urls')),  # запчасти
    path('api/service/', include('service_requests.urls')),  # СТО / детейлинг

    # FRONTEND REQUEST PARTS
    path(
        'request-parts/',
        TemplateView.as_view(
            template_name='request-parts/index.html'
        )
    ),

    path(
        'request-parts/cabinet/',
        TemplateView.as_view(
            template_name='request-parts/cabinet/index.html'
        ),
    ),

    path(
        'request-parts/register/',
        TemplateView.as_view(
            template_name='request-parts/register/index.html'
        ),
        name='request_parts_register',
    ),

    path(
        'request-parts/guide/',
        TemplateView.as_view(
            template_name='request-parts/guide/index.html'
        ),
        name='request_parts_guide',
    ),

    path(
        'request-parts/faq/',
        TemplateView.as_view(
            template_name='request-parts/faq/index.html'
        ),
        name='request_parts_faq',
    ),

    path(
        'my-request/<int:req_id>/',
        view_request_status,
        name='view_request_status_public',
    ),

    # КАТАЛОГ ПРОДАВЦОВ ЗАПЧАСТЕЙ (НОВЫЙ КРАСИВЫЙ URL)
    path(
        'parts-sellers/',
        parts_sellers_catalog,
        name='parts_sellers_catalog_public',
    ),

    path(
        'parts-seller/<int:seller_id>/',
        parts_seller_detail,
        name='parts_seller_detail_public',
    ),

    # СТО / ДЕТЕЙЛИНГ
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
        'service-request/guide/',
        TemplateView.as_view(
            template_name='service-request/guide/index.html'
        ),
        name='service_request_guide',
    ),

    path(
        'service-request/faq/',
        TemplateView.as_view(
            template_name='service-request/faq/index.html'
        ),
        name='service_request_faq',
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

    path('market/', include('catalog.urls')),
    path('', include('catalog.urls')),

]

# parts sellers routes

from django.urls import re_path

from backend.media_views import serve_media


urlpatterns += [
    re_path(
        r'^products/(?P<path>.*)$',
        serve_media,
        name='media',
    ),
]