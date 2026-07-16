from django.urls import path

from marketing.views import ContactsView, DashboardView, StubView

app_name = 'marketing'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('contacts/', ContactsView.as_view(), name='contacts'),
    path(
        'audiences/',
        StubView.as_view(),
        {'section': 'audiences'},
        name='audiences',
    ),
    path(
        'campaigns/',
        StubView.as_view(),
        {'section': 'campaigns'},
        name='campaigns',
    ),
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
