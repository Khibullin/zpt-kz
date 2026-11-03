from django.urls import path
from . import views, document_views
app_name = 'inventory'
urlpatterns = [
    path('items/',document_views.items,name='items'),
    path('documents/',document_views.documents,name='documents'),
    path('documents/<int:pk>/action/',document_views.document_action,name='document_action'),
    path('cash/',document_views.cash,name='cash'),
    path('new/<str:kind>/',document_views.create,name='create'),
path('',views.dashboard,name='overview'),path('stock/',views.dashboard,{'section':'stock'},name='stock'),path('sales/',views.dashboard,{'section':'sales'},name='sales'),path('finance/',views.dashboard,{'section':'finance'},name='finance'),path('upload/',views.upload,name='upload')]
