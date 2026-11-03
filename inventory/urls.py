from django.urls import path
from . import views
app_name = 'inventory'
urlpatterns = [path('',views.dashboard,name='overview'),path('stock/',views.dashboard,{'section':'stock'},name='stock'),path('sales/',views.dashboard,{'section':'sales'},name='sales'),path('finance/',views.dashboard,{'section':'finance'},name='finance'),path('upload/',views.upload,name='upload')]
