from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from . import views

app_name = 'procurement'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', LoginView.as_view(template_name='procurement/login.html', next_page='/office/procurement/'), name='login'),
    path('logout/', LogoutView.as_view(next_page='/office/procurement/login/'), name='logout'),
    path('settings/', views.settings, name='settings'),
    path('upload/', views.upload, name='upload'),
    path('users/', views.users, name='users'),
    path('basket/', views.basket, name='basket'),
    path('export/', views.export, name='export'),
    path('selection/<int:pk>/remove/', views.remove, name='remove'),
    path('product/<int:pk>/', views.detail, name='detail'),
    path('product/<int:pk>/select/', views.select, name='select'),
    path('product/<int:pk>/draft/', views.draft, name='draft'),
    path('product/<int:pk>/verify/', views.verify, name='verify'),
    path('product/<int:pk>/favorite/', views.favorite, name='favorite'),
]
