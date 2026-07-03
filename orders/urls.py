from django.urls import path

from . import views

app_name = 'orders'

urlpatterns = [
    path('cart/', views.cart_view, name='cart'),
    path('cart/count/', views.cart_count_api, name='cart_count'),
    path('cart/add/', views.cart_add, name='cart_add_api'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/add/virtual/', views.cart_add_virtual, name='cart_add_virtual'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('cart/update_quantity/', views.cart_update_quantity, name='cart_update_quantity'),
    path('cart/checkout/', views.checkout, name='checkout'),
    path(
        'orders/<int:order_id>/<uuid:access_token>/success/',
        views.order_success,
        name='order_success',
    ),
]
