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
        'templates/',
        StubView.as_view(),
        {'section': 'templates'},
        name='templates',
    ),
    path(
        'service-notifications/',
        StubView.as_view(),
        {'section': 'service_notifications'},
        name='service_notifications',
    ),
    path(
        'history/',
        StubView.as_view(),
        {'section': 'history'},
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
