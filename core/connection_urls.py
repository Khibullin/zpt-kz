from django.urls import path
from core import connection_views as views

urlpatterns = [
    path('seller/login/', views.seller_login, name='connection_login'),
    path('seller/join/', views.seller_join, name='connection_join'),
    path('seller/quality/', views.seller_quality, name='connection_quality'),
    path('requests/', views.feed, name='connection_feed'),
    path('request/<int:req_id>/', views.detail, name='connection_detail'),
    path('request/<int:req_id>/contact/<str:channel>/', views.seller_contact, name='connection_seller_contact'),
    path('buyer/<int:req_id>/<uuid:access_token>/', views.buyer, name='connection_buyer'),
    path('buyer/<int:req_id>/<uuid:access_token>/<int:seller_id>/<str:channel>/', views.buyer_contact, name='connection_buyer_contact'),
]
