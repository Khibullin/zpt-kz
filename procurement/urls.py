from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views
from .auth import WorkspaceLoginView
from .invitations import invite_manager, accept_invitation, activate_invitation

app_name = 'procurement'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', WorkspaceLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='/office/procurement/login/'), name='logout'),
    path('settings/', views.settings, name='settings'),
    path('upload/', views.upload, name='upload'),
    path('users/', views.users, name='users'),
    path('users/invite/', invite_manager, name='invite_manager'),
    path('invitation/<str:token>/', accept_invitation, name='accept_invitation'),
    path('activate/', activate_invitation, name='activate_invitation'),
    path('basket/', views.basket, name='basket'),
    path('purchases/new/<int:supplier_id>/', views.purchase_confirm, name='purchase_confirm'),
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
