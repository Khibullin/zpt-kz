from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

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
        'service-request/register/',
        TemplateView.as_view(
            template_name='service-request/register/index.html'
        )
    ),
]
