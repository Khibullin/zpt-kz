from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views
from . import price_update_views
from inventory.purchase_receiving import receive_purchase
from .auth import WorkspaceLoginView
from .invitations import invite_manager, accept_invitation, activate_invitation
from .retail_views import retail_workspace, retail_excel
from .kaspi_card_views import purchased_products as kaspi_products, card_pricing_settings

app_name = 'procurement'
urlpatterns = [
    path('kaspi-products/pricing/', card_pricing_settings, name='kaspi_card_pricing'),
    path('kaspi-products/', kaspi_products, name='kaspi_products'),
    path('fitments/refresh/', views.refresh_shared_fitments, name='refresh_shared_fitments'),
    path('price-updates/<int:pk>/retry-kaspi/', price_update_views.retry_kaspi, name='price_update_retry_kaspi'),
    path('price-updates/', price_update_views.updates, name='price_updates'),
    path('price-updates/queue/', price_update_views.queue_export, name='price_review_queue'),
    path('price-updates/<int:pk>/advance/', price_update_views.advance, name='price_update_advance'),
    path('retail/', retail_workspace, name='retail'),
    path('retail/excel/', retail_excel, name='retail_excel'),
    path('', views.dashboard, name='dashboard'),
    path('login/', WorkspaceLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='/office/procurement/login/'), name='logout'),
    path('settings/', views.settings, name='settings'),
    path('upload/', views.upload, name='upload'),
    path('users/', views.users, name='users'),
    path('users/invite/', invite_manager, name='invite_manager'),
    path('invitation/<str:token>/', accept_invitation, name='accept_invitation'),
    path('activate/', activate_invitation, name='activate_invitation'),
    path('suppliers/<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('basket/', views.basket, name='basket'),
    path('purchases/new/<int:supplier_id>/', views.purchase_confirm, name='purchase_confirm'),
    path('purchases/<int:pk>/receive/', receive_purchase, name='purchase_receive'),
    path('purchases/<int:pk>/', views.purchase_detail, name='purchase_detail'),
    path('purchases/<int:pk>/excel/', views.purchase_excel, name='purchase_excel'),
    path('purchases/<int:pk>/documents/<int:document_id>/', views.purchase_document, name='purchase_document'),
    path('export/', views.export, name='export'),
    path('selection/<int:pk>/remove/', views.remove, name='remove'),
    path('product/<int:pk>/', views.detail, name='detail'),
    path('product/<int:pk>/select/', views.select, name='select'),
    path('product/<int:pk>/draft/', views.draft, name='draft'),
    path('product/<int:pk>/verify/', views.verify, name='verify'),
    path('product/<int:pk>/favorite/', views.favorite, name='favorite'),
]
