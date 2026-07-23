from django.urls import path

from marketing.audience_views import (
    AudienceCalculateView,
    AudienceCopyView,
    AudienceCreateView,
    AudienceDeleteView,
    AudienceDetailView,
    AudienceListView,
    AudienceUpdateView,
)
from marketing.campaign_send_views import (
    CampaignTestSendExecuteView,
    CampaignTestSendPreflightView,
)
from marketing.campaign_views import (
    CampaignArchiveView,
    CampaignCancelView,
    CampaignCopyView,
    CampaignCreateView,
    CampaignDeleteView,
    CampaignDetailView,
    CampaignListView,
    CampaignPrepareView,
    CampaignUpdateView,
)
from marketing.history_views import MarketingHistoryView
from marketing.template_views import (
    TemplateActivateView,
    TemplateCopyView,
    TemplateCreateView,
    TemplateDeactivateView,
    TemplateDeleteView,
    TemplateDetailView,
    TemplateListView,
    TemplateUpdateView,
)
from marketing.views import ContactsView, DashboardView, StubView

app_name = 'marketing'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('contacts/', ContactsView.as_view(), name='contacts'),
    path('audiences/', AudienceListView.as_view(), name='audiences'),
    path('audiences/new/', AudienceCreateView.as_view(), name='audience_create'),
    path('audiences/<int:pk>/', AudienceDetailView.as_view(), name='audience_detail'),
    path('audiences/<int:pk>/edit/', AudienceUpdateView.as_view(), name='audience_edit'),
    path(
        'audiences/<int:pk>/calculate/',
        AudienceCalculateView.as_view(),
        name='audience_calculate',
    ),
    path('audiences/<int:pk>/copy/', AudienceCopyView.as_view(), name='audience_copy'),
    path('audiences/<int:pk>/delete/', AudienceDeleteView.as_view(), name='audience_delete'),
    path('campaigns/', CampaignListView.as_view(), name='campaigns'),
    path('campaigns/new/', CampaignCreateView.as_view(), name='campaign_create'),
    path('campaigns/<int:pk>/', CampaignDetailView.as_view(), name='campaign_detail'),
    path('campaigns/<int:pk>/edit/', CampaignUpdateView.as_view(), name='campaign_edit'),
    path('campaigns/<int:pk>/prepare/', CampaignPrepareView.as_view(), name='campaign_prepare'),
    path('campaigns/<int:pk>/copy/', CampaignCopyView.as_view(), name='campaign_copy'),
    path('campaigns/<int:pk>/cancel/', CampaignCancelView.as_view(), name='campaign_cancel'),
    path('campaigns/<int:pk>/archive/', CampaignArchiveView.as_view(), name='campaign_archive'),
    path('campaigns/<int:pk>/delete/', CampaignDeleteView.as_view(), name='campaign_delete'),
    path(
        'campaigns/<int:pk>/test-send/preflight/',
        CampaignTestSendPreflightView.as_view(),
        name='campaign_test_send_preflight',
    ),
    path(
        'campaigns/<int:pk>/test-send/execute/',
        CampaignTestSendExecuteView.as_view(),
        name='campaign_test_send_execute',
    ),
    path(
        'templates/',
        TemplateListView.as_view(),
        name='templates',
    ),
    path('templates/new/', TemplateCreateView.as_view(), name='template_create'),
    path('templates/<int:pk>/', TemplateDetailView.as_view(), name='template_detail'),
    path('templates/<int:pk>/edit/', TemplateUpdateView.as_view(), name='template_edit'),
    path('templates/<int:pk>/copy/', TemplateCopyView.as_view(), name='template_copy'),
    path('templates/<int:pk>/activate/', TemplateActivateView.as_view(), name='template_activate'),
    path(
        'templates/<int:pk>/deactivate/',
        TemplateDeactivateView.as_view(),
        name='template_deactivate',
    ),
    path('templates/<int:pk>/delete/', TemplateDeleteView.as_view(), name='template_delete'),
    path(
        'service-notifications/',
        StubView.as_view(),
        {'section': 'service_notifications'},
        name='service_notifications',
    ),
    path(
        'history/',
        MarketingHistoryView.as_view(),
        name='history',
    ),
    path(
        'unsubscribes/',
        StubView.as_view(),
        {'section': 'unsubscribes'},
        name='unsubscribes',
    ),
    path(
        'settings/',
        StubView.as_view(),
        {'section': 'settings'},
        name='settings',
    ),
]
