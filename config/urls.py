from django.contrib import admin
from django.urls import path
from core.views import home, testpage

urlpatterns = [
    path('admin/', admin.site.urls),

    path('', home, name='home'),  # текущая главная
    path('testpage/', testpage, name='testpage'),  # новая главная
]