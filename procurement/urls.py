from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views
from . import eep_comparison
from . import apify_review
from . import comparison_views
from . import comparison_clipboard
from . import comparison_first_ten
from . import comparison_next_fifty
from . import price_update_views
from . import selection_rule_views
from . import selection_exports
from inventory.purchase_receiving import receive_purchase
from .auth import WorkspaceLoginView
from .invitations import invite_manager, accept_invitation, activate_invitation
from .retail_views import retail_workspace, retail_excel
from .kaspi_card_views import purchased_products as kaspi_products, card_pricing_settings
from .kaspi_catalog_api import shared_image
from .kaspi_price_feed import price_feed

app_name = 'procurement'
urlpatterns = [
    path('selection-rules/', selection_rule_views.selection_rules, name='selection_rules'),
    path('selection-rules/rules/', selection_rule_views.omega_selection_rules_help, name='omega_selection_rules_help'),
    path('selection-rules/export/excel/', selection_exports.selection_excel, name='selection_excel'),
    path('selection-rules/export/apify-json/', selection_exports.selection_apify_json, name='selection_apify_json'),
    path('kaspi-feed/<str:token>/', price_feed, name='kaspi_price_feed'),
    path('kaspi-photo/<str:token>/', shared_image, name='kaspi_shared_image'),
    path('kaspi-products/pricing/', card_pricing_settings, name='kaspi_card_pricing'),
    path('kaspi-products/', kaspi_products, name='kaspi_products'),
    path('fitments/refresh/', views.refresh_shared_fitments, name='refresh_shared_fitments'),
    path('price-updates/<int:pk>/retry-kaspi/', price_update_views.retry_kaspi, name='price_update_retry_kaspi'),
    path('price-updates/', price_update_views.updates, name='price_updates'),
    path('processing/', views.product_processing, name='product_processing'),
    path('kaspi-data/', views.kaspi_data, name='kaspi_data'),
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
    path('basket/excel/', views.basket_excel, name='basket_excel'),
    path('basket/add-selected/', views.select_many, name='select_many'),
    path('basket/remove-selected/', views.remove_selected, name='remove_selected'),
    path('purchases/new/<int:supplier_id>/', views.purchase_confirm, name='purchase_confirm'),
    path('purchases/<int:pk>/receive/', receive_purchase, name='purchase_receive'),
    path('purchases/<int:pk>/', views.purchase_detail, name='purchase_detail'),
    path('purchases/<int:pk>/excel/', views.purchase_excel, name='purchase_excel'),
    path('purchases/<int:pk>/documents/<int:document_id>/', views.purchase_document, name='purchase_document'),
    path('export/', views.export, name='export'),
    path('selection/<int:pk>/remove/', views.remove, name='remove'),
    path('apify-review/', apify_review.apify_review, name='apify_review'),
    path('comparison/top-10/', comparison_first_ten.comparison_first_ten, name='comparison_first_ten'),
    path('comparison/next-50/', comparison_next_fifty.comparison_next_fifty, name='comparison_next_fifty'),
    path('comparison/run-one/', comparison_views.run_one_comparison, name='comparison_run_one'),
    path('comparison/<int:pk>/', eep_comparison.comparison, name='comparison'),
    path('comparison/<int:pk>/paste-screenshot/', comparison_clipboard.paste_kaspi_screenshot, name='comparison_paste_screenshot'),
    path('comparison/<int:pk>/manage-screenshot/', comparison_clipboard.manage_kaspi_screenshot, name='comparison_manage_screenshot'),
    path('comparison/<int:pk>/recognize-screenshots/', comparison_clipboard.recognize_kaspi_screenshots, name='comparison_recognize_screenshots'),
    path('comparison/<int:pk>/confirm-screenshots/', comparison_clipboard.confirm_kaspi_screenshots, name='comparison_confirm_screenshots'),
    path('comparison/<int:pk>/review-identity/', comparison_clipboard.review_kaspi_identity, name='comparison_review_identity'),
    path('comparison/eep-59a200068/', eep_comparison.eep_comparison, name='eep_comparison'),
    path('product/<int:pk>/', views.detail, name='detail'),
    path('product/<int:pk>/select/', views.select, name='select'),
    path('product/<int:pk>/draft/', views.draft, name='draft'),
    path('product/<int:pk>/verify/', views.verify, name='verify'),
    path('product/<int:pk>/favorite/', views.favorite, name='favorite'),
]
