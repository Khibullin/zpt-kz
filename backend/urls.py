from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),

    path('api/', include('core.urls')),  # запчасти
    path('api/service/', include('service_requests.urls')),  # СТО / детейлинг

    path('service-request/', TemplateView.as_view(template_name='service-request/index.html')),
]