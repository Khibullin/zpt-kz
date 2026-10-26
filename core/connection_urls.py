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

from core import workflow_views as workflow
urlpatterns += [
    path('seller/connect/', workflow.connect, name='workflow_connect'),
    path('seller/claim/', workflow.claim, name='workflow_claim'),
    path('seller/claim/<int:seller_id>/', workflow.claim_confirm, name='workflow_claim_confirm'),
    path('seller/access-status/', workflow.claim_status, name='workflow_claim_status'),
    path('seller/access/<uuid:token>/', workflow.redeem_access, name='workflow_redeem'),
    path('seller/edit/', workflow.profile_edit, name='workflow_profile_edit'),
    path('seller/channels/', workflow.channels, name='workflow_channels'),
    path('seller/telegram/', workflow.telegram_connect, name='workflow_telegram_connect'),
    path('buyer/<int:req_id>/<uuid:access_token>/step/<str:step>/', workflow.buyer_step, name='workflow_buyer_step'),
    path('buyer/<int:req_id>/<uuid:access_token>/shop/<int:seller_id>/', workflow.buyer_shop, name='workflow_buyer_shop'),
]
urlpatterns += [
    path('request/<int:req_id>/contact/', workflow.seller_contact_page, name='workflow_seller_contact_page'),
    path('request/<int:req_id>/result/', workflow.seller_result, name='workflow_seller_result'),
]
from core.telegram_connection_views import telegram_connection_webhook
urlpatterns += [path('telegram/webhook/', telegram_connection_webhook, name='workflow_telegram_webhook')]
from core.workflow_admin import urlpatterns as staff_urls
urlpatterns += staff_urls
urlpatterns += [path('seller/telegram-login/', workflow.telegram_login, name='workflow_telegram_login')]
urlpatterns += [path('buyer/<int:req_id>/<uuid:access_token>/channels/', workflow.buyer_channels, name='workflow_buyer_channels')]
urlpatterns += [path('buyer/<int:req_id>/<uuid:access_token>/directory/<int:seller_id>/<str:channel>/', workflow.buyer_directory_contact, name='workflow_buyer_directory_contact')]

from core import buyer_selection_views
urlpatterns += [
    path('buyer/<int:req_id>/<uuid:access_token>/selection/<str:step>/', buyer_selection_views.workspace, name='buyer_selection'),
    path('buyer/<int:req_id>/<uuid:access_token>/select/<int:seller_id>/', buyer_selection_views.toggle, name='buyer_selection_toggle'),
]
from core.buyer_selection_preview import preview as buyer_selection_preview
urlpatterns += [path('buyer-preview/', buyer_selection_preview, name='buyer_selection_preview')]
