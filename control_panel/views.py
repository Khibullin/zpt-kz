from control_panel.auth import control_staff_required
from control_panel.periods import (
    DEFAULT_OVERVIEW_PERIOD,
    LIST_PERIOD_CHOICES,
    PERIOD_CHOICES,
    normalize_period,
)
from control_panel.selectors.kaspi_products import list_kaspi_products
from control_panel.selectors.overview import overview_context
from control_panel.selectors.parts_requests import (
    get_parts_request_detail,
    list_parts_requests,
)
from control_panel.selectors.sellers import get_seller_detail, list_sellers
from control_panel.selectors.service_requests import (
    get_service_request_detail,
    list_service_requests,
)
from control_panel.selectors.sto import get_sto_detail, list_sto
from django.shortcuts import render


NAV_SECTIONS = (
    {
        'title': '',
        'items': (('overview', 'Обзор', 'control_panel:overview'),),
    },
    {
        'title': 'Клиенты и спрос',
        'items': (
            ('parts', 'Заявки на запчасти', 'control_panel:parts_request_list'),
            ('services', 'Заявки СТО', 'control_panel:service_request_list'),
        ),
    },
    {
        'title': 'Партнёры',
        'items': (
            ('sellers', 'Продавцы', 'control_panel:seller_list'),
            ('sto', 'СТО', 'control_panel:sto_list'),
        ),
    },
    {
        'title': 'Товары',
        'items': (
            ('kaspi_products', 'Товары и цены', 'control_panel:kaspi_product_list'),
        ),
    },
    {
        'title': 'Система',
        'items': (('admin', 'Техническая Admin', 'admin:index'),),
    },
)

FUTURE_MODULES = (
    'Заказы',
    'Склады',
    'Закупки',
    'Маркетинг',
    'Аналитика',
)


def _base_context(*, active_nav: str, breadcrumbs: list[dict], title: str):
    return {
        'active_nav': active_nav,
        'nav_sections': NAV_SECTIONS,
        'breadcrumbs': breadcrumbs,
        'page_title': title,
        'period_choices': PERIOD_CHOICES,
        'list_period_choices': LIST_PERIOD_CHOICES,
    }


@control_staff_required
def overview(request):
    period = normalize_period(
        request.GET.get('period'),
        default=DEFAULT_OVERVIEW_PERIOD,
        allowed=tuple(key for key, _label in PERIOD_CHOICES),
    )
    context = _base_context(
        active_nav='overview',
        breadcrumbs=[{'label': 'Обзор'}],
        title='Обзор',
    )
    context.update(overview_context(period))
    context['period'] = period
    context['future_modules'] = FUTURE_MODULES
    return render(request, 'control_panel/overview.html', context)


@control_staff_required
def parts_request_list(request):
    context = _base_context(
        active_nav='parts',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки на запчасти'},
        ],
        title='Заявки на запчасти',
    )
    context.update(list_parts_requests(request.GET))
    return render(request, 'control_panel/parts_list.html', context)


@control_staff_required
def parts_request_detail(request, pk: int):
    detail = get_parts_request_detail(pk)
    context = _base_context(
        active_nav='parts',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки на запчасти', 'url': 'control_panel:parts_request_list'},
            {'label': f'Заявка №{pk}'},
        ],
        title=f'Заявка №{pk}',
    )
    context.update(detail)
    return render(request, 'control_panel/parts_detail.html', context)


@control_staff_required
def service_request_list(request):
    context = _base_context(
        active_nav='services',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки СТО'},
        ],
        title='Заявки СТО',
    )
    context.update(list_service_requests(request.GET))
    return render(request, 'control_panel/services_list.html', context)


@control_staff_required
def service_request_detail(request, pk: int):
    detail = get_service_request_detail(pk)
    context = _base_context(
        active_nav='services',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки СТО', 'url': 'control_panel:service_request_list'},
            {'label': f'Заявка СТО №{pk}'},
        ],
        title=f'Заявка СТО №{pk}',
    )
    context.update(detail)
    return render(request, 'control_panel/services_detail.html', context)


@control_staff_required
def seller_list(request):
    context = _base_context(
        active_nav='sellers',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Продавцы'},
        ],
        title='Продавцы',
    )
    context.update(list_sellers(request.GET))
    return render(request, 'control_panel/sellers_list.html', context)


@control_staff_required
def seller_detail(request, pk: int):
    detail = get_seller_detail(pk)
    context = _base_context(
        active_nav='sellers',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Продавцы', 'url': 'control_panel:seller_list'},
            {'label': detail['seller'].name},
        ],
        title=detail['seller'].name,
    )
    context.update(detail)
    return render(request, 'control_panel/sellers_detail.html', context)


@control_staff_required
def sto_list(request):
    context = _base_context(
        active_nav='sto',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'СТО'},
        ],
        title='СТО',
    )
    context.update(list_sto(request.GET))
    return render(request, 'control_panel/sto_list.html', context)


@control_staff_required
def sto_detail(request, pk: int):
    detail = get_sto_detail(pk)
    context = _base_context(
        active_nav='sto',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'СТО', 'url': 'control_panel:sto_list'},
            {'label': detail['seller'].name},
        ],
        title=detail['seller'].name,
    )
    context.update(detail)
    return render(request, 'control_panel/sto_detail.html', context)


@control_staff_required
def kaspi_product_list(request):
    context = _base_context(
        active_nav='kaspi_products',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Товары и цены'},
        ],
        title='Товары и цены',
    )
    context.update(list_kaspi_products(request.GET))
    return render(request, 'control_panel/kaspi_products.html', context)
