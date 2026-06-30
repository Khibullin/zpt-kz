from django.urls import path

from . import views

app_name = 'orders'

urlpatterns = [
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('cart/checkout/', views.checkout, name='checkout'),
    path(
        'orders/<int:order_id>/mock-kaspi-payment/',
        views.mock_kaspi_payment,
        name='mock_kaspi_payment',
    ),
    path(
        'orders/<int:order_id>/success/',
        views.order_success,
        name='order_success',
    ),
]
