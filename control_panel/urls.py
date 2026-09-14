from django.urls import path

from control_panel import views

app_name = 'control_panel'

urlpatterns = [
    path('', views.overview, name='overview'),
    path('requests/parts/', views.parts_request_list, name='parts_request_list'),
    path(
        'requests/parts/<int:pk>/',
        views.parts_request_detail,
        name='parts_request_detail',
    ),
    path('requests/services/', views.service_request_list, name='service_request_list'),
    path(
        'requests/services/<int:pk>/',
        views.service_request_detail,
        name='service_request_detail',
    ),
    path('partners/sellers/', views.seller_list, name='seller_list'),
    path(
        'partners/sellers/<int:pk>/',
        views.seller_detail,
        name='seller_detail',
    ),
    path('partners/services/', views.sto_list, name='sto_list'),
    path(
        'partners/services/<int:pk>/',
        views.sto_detail,
        name='sto_detail',
    ),
]
