from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

from core.views import (
    parts_sellers_catalog,
    parts_seller_detail,
)

from service_requests.views import (
    service_request_result,
    services_catalog,
    service_seller_detail,
)


urlpatterns = [
    path('admin/', admin.site.urls),

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
        )
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

    path('', include('catalog.urls')),
    path('market/', include('catalog.urls')),

]

# parts sellers routes

from django.conf import settings
from django.views.static import serve
from django.urls import re_path


urlpatterns += [
    re_path(
        r'^products/(?P<path>.*)$',
        serve,
        {
            'document_root': settings.BASE_DIR / 'products',
        }
    ),
]