from django.urls import path
from .views import (
    catalog_list,
    product_detail,
    seller_register,
    seller_login,
    seller_logout,
    seller_dashboard,
    seller_profile,
    seller_profile_edit,
    seller_profile_delete,
    seller_change_password,
    public_seller_profile,
    add_product,
    edit_product,
    delete_product,
    load_brands,
    load_models,
    load_compatible_models,
    faq_view,
    feedback_view,
)

urlpatterns = [
    path('', catalog_list, name='catalog_list'),

    path('seller/register/', seller_register, name='seller_register'),
    path('seller/login/', seller_login, name='seller_login'),
    path('seller/logout/', seller_logout, name='seller_logout'),
    path('seller/dashboard/', seller_dashboard, name='seller_dashboard'),
    path('seller/profile/', seller_profile, name='seller_profile'),
    path('seller/profile/edit/', seller_profile_edit, name='seller_profile_edit'),
    path(
        'seller/profile/change-password/',
        seller_change_password,
        name='seller_change_password'
    ),
    path('seller/profile/delete/', seller_profile_delete, name='seller_profile_delete'),

    path('seller/add/', add_product, name='add_product'),
    path('seller/edit/<int:pk>/', edit_product, name='edit_product'),
    path('seller/delete/<int:pk>/', delete_product, name='delete_product'),

    path('ajax/load-brands/', load_brands, name='ajax_load_brands'),
    path('ajax/load-models/', load_models, name='ajax_load_models'),
    path(
        'ajax/load-compatible-models/',
        load_compatible_models,
        name='ajax_load_compatible_models'
    ),

    path('faq/', faq_view, name='faq'),
    path('feedback/', feedback_view, name='feedback'),

    path('<int:pk>/', product_detail, name='product_detail_old'),
    path('<slug:slug>/', product_detail, name='product_detail'),

    path(
        'seller/<slug:slug>/',
        public_seller_profile,
        name='public_seller_profile'
    ),
]
