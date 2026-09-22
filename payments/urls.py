from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    path('robokassa/result/', views.robokassa_result, name='robokassa_result'),
    path('robokassa/success/', views.robokassa_success, name='robokassa_success'),
    path('robokassa/fail/', views.robokassa_fail, name='robokassa_fail'),
]
