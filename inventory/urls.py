from django.urls import path
from . import views, document_views, matching_views, tools_views
app_name = 'inventory'
urlpatterns = [
    path('cash/check/',tools_views.bank_check,name='bank_check'),
    path('documents/<int:pk>/amounts/',tools_views.financial_details,name='financial_details'),
    path('edit/<str:kind>/<int:pk>/',tools_views.edit,name='edit'),
    path('documents/<int:pk>/cancel/',tools_views.cancel_document,name='cancel_document'),
    path('cash/transfer/',tools_views.transfer_money,name='transfer_money'),
    path('cash/<int:pk>/cancel/',tools_views.cancel_cash,name='cancel_cash'),
    path('openings/',tools_views.prepare_openings,name='prepare_openings'),
    path('settlements/',tools_views.settlements,name='settlements'),
    path('results/',tools_views.operating_report,name='operating_report'),
    path('reconciliation/',matching_views.reconciliation,name='reconciliation'),
    path('reconciliation/prepare/',matching_views.prepare,name='prepare_items'),
    path('reconciliation/confirm/',matching_views.confirm,name='confirm_mapping'),
    path('costs/',document_views.cost_report,name='cost_report'),
    path('items/',document_views.items,name='items'),
    path('documents/',document_views.documents,name='documents'),
    path('documents/<int:pk>/action/',document_views.document_action,name='document_action'),
    path('cash/',document_views.cash,name='cash'),
    path('new/<str:kind>/',document_views.create,name='create'),
path('',views.dashboard,name='overview'),path('stock/',views.dashboard,{'section':'stock'},name='stock'),path('sales/',views.dashboard,{'section':'sales'},name='sales'),path('finance/',views.dashboard,{'section':'finance'},name='finance'),path('upload/',views.upload,name='upload')]
