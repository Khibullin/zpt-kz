from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.views.generic import TemplateView

from core.services.buyer_broadcast_settings import get_buyer_broadcast_mode
from marketing.permissions import user_can_access_marketing_cabinet
from marketing.services.contacts import (
    CATEGORY_PERIOD_CHOICES,
    CATEGORY_SOURCE_CHOICES,
    CONTACT_TABS,
    ROLE_LABELS,
    ContactFilters,
    build_contact_registry,
    filter_options,
    list_contacts,
    sort_contacts,
)
from marketing.services.dashboard import get_group_cards, get_overview_stats

CONTACTS_PAGE_SIZE = 50

NAV_ITEMS = (
    ('dashboard', 'Обзор', 'marketing:dashboard'),
    ('contacts', 'Контакты', 'marketing:contacts'),
    ('audiences', 'Аудитории', 'marketing:audiences'),
    ('campaigns', 'Кампании', 'marketing:campaigns'),
    ('templates', 'Шаблоны WhatsApp', 'marketing:templates'),
    ('service_notifications', 'Сервисные уведомления', 'marketing:service_notifications'),
    ('history', 'История отправок', 'marketing:history'),
    ('unsubscribes', 'Отписки и ошибки', 'marketing:unsubscribes'),
    ('settings', 'Настройки', 'marketing:settings'),
)

STUB_TITLES = {
    'audiences': 'Аудитории',
    'campaigns': 'Кампании',
    'templates': 'Шаблоны WhatsApp',
    'service_notifications': 'Сервисные уведомления',
    'history': 'История отправок',
    'unsubscribes': 'Отписки и ошибки',
    'settings': 'Настройки',
}


class MarketingCabinetMixin:
    login_url = '/admin/login/'
    active_nav = ''

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), self.login_url)
        if not user_can_access_marketing_cabinet(request.user):
            return HttpResponseForbidden('Доступ к кабинету маркетинга запрещён.')
        return super().dispatch(request, *args, **kwargs)

    def get_broadcast_mode_context(self) -> dict:
        mode = get_buyer_broadcast_mode()
        return {
            'broadcast_mode': mode,
            'broadcast_mode_label': mode,
        }

    def get_nav_context(self) -> dict:
        return {
            'nav_items': NAV_ITEMS,
            'active_nav': self.active_nav,
        }


class DashboardView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/dashboard.html'
    active_nav = 'dashboard'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        context['overview_stats'] = get_overview_stats()
        context['group_cards'] = get_group_cards()
        return context


class ContactsView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/contacts.html'
    active_nav = 'contacts'

    def get_filters(self) -> ContactFilters:
        params = self.request.GET
        return ContactFilters(
            q=params.get('q', '').strip(),
            tab=params.get('tab', 'all'),
            contact_type=params.get('contact_type', '').strip(),
            role=params.get('role', '').strip(),
            country=params.get('country', '').strip(),
            city=params.get('city', '').strip(),
            activity_status=params.get('activity_status', '').strip(),
            marketing_consent=params.get('marketing_consent', '').strip(),
            last_activity_from=params.get('last_activity_from', '').strip(),
            last_activity_to=params.get('last_activity_to', '').strip(),
            is_test=params.get('is_test', '').strip(),
            transport_type=params.get('transport_type', '').strip(),
            brand=params.get('brand', '').strip(),
            model=params.get('model', '').strip(),
            category=params.get('category', '').strip(),
            category_source=params.get('category_source', '').strip(),
            category_period=params.get('category_period', '').strip(),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        filters = self.get_filters()
        registry = build_contact_registry()
        contacts = sort_contacts(list_contacts(filters))
        paginator = Paginator(contacts, CONTACTS_PAGE_SIZE)
        page_number = self.request.GET.get('page', '1')
        page_obj = paginator.get_page(page_number)
        context['filters'] = filters
        context['page_obj'] = page_obj
        context['contacts'] = page_obj.object_list
        context['contact_tabs'] = CONTACT_TABS
        context['role_labels'] = ROLE_LABELS
        context['filter_options'] = filter_options(registry)
        context['category_period_choices'] = CATEGORY_PERIOD_CHOICES
        context['category_source_choices'] = CATEGORY_SOURCE_CHOICES
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        context['pagination_querystring'] = query_params.urlencode()
        return context


class StubView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/stub.html'

    def dispatch(self, request, *args, **kwargs):
        self.active_nav = kwargs.get('section', '')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        section = kwargs.get('section', '')
        context['section_title'] = STUB_TITLES.get(section, 'Раздел')
        return context
