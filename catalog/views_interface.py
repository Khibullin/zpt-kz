"""Navigation for the approved homepage; existing transactional views stay authoritative."""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse


def homepage_tiles():
    section = lambda name: reverse('interface_section', kwargs={'section': name})
    return [
        {'key': 'parts', 'url': reverse('interface_find_part'), 'art': 'parts', 'caption': 'partsHint'},
        {'key': 'dismantle', 'url': section('dismantle'), 'title': 'dismantleEntry', 'art': 'dismantle', 'caption': 'dismantleHint'},
        {'key': 'consumables', 'url': reverse('consumables_landing'), 'art': 'kits', 'caption': 'consumablesHint'},
        {'key': 'serviceHub', 'url': reverse('services_landing'), 'art': 'repair', 'caption': 'serviceHubHint'},
        {'key': 'wholesale', 'url': reverse('public_seller_wholesale', kwargs={'slug':'ag-parts'}), 'art': 'wholesale', 'caption': 'wholesaleHint'},
    ]


def interface_section(request, section):
    if section in ('repair', 'detailing'):
        # Preserve existing filtered links; the plain section URL is the entry page.
        if request.GET:
            from service_requests.catalog_pages import service_directory
            return service_directory(request, section=section)
        return organization_entry(request, section)
    parts = reverse('interface_find_part')
    used = parts + '?condition=used'
    if section == 'dismantle':
        return render(request, 'catalog/interface/dismantlers/landing.html', {
            'section_title': 'dismantle',
            'hide_product_search': True,
        })
    config = {
        'choose': ('request', [('parts', parts, 'gear'), ('usedRequest', used, 'gear'),
                              ('repairRequest', '/service-request/?type=sto', 'garage'),
                              ('detailRequest', '/service-request/?type=detailing', 'garage')]),
        'dismantle': ('dismantle', [('usedRequest', used, 'file'),
                                   ('dismantlers', reverse('dismantlers_catalog'), 'gear')]),
        'repair': ('repair', [('repairRequest', '/service-request/?type=sto', 'file'),
                             ('services', '/catalog/services/?type=sto', 'garage')]),
        'detailing': ('detailing', [('detailRequest', '/service-request/?type=detailing', 'file'),
                                   ('services', '/catalog/services/?type=detailing', 'garage')]),
        'account': ('account', [('accountShop', reverse('seller_dashboard'), 'store'),
                               ('accountParts', '/request-parts/cabinet/', 'gear'),
                               ('accountService', '/service-request/cabinet/', 'garage')]),
    }
    if section not in config:
        raise Http404
    title, choices = config[section]
    return render(request, 'catalog/interface/section.html', {
        'section_title': title,
        'choices': [{'key': key, 'url': url, 'icon': icon} for key, url, icon in choices],
    })


def consumables_landing(request):
    """Read-only selection of live consumables; checkout stays in the existing cards."""
    from django.db.models import Q
    from .models import Product
    from .views import attach_sellers_to_products
    from .wholesale import attach_public_wholesale_flags, public_wholesale_prefetch

    # Unicode-insensitive Russian matches on both PostgreSQL and SQLite.
    # SQLite's LIKE/LOWER do not fold Cyrillic case, unlike the regex operator.
    products = Product.objects.filter(status='active').filter(
        Q(title__iregex=r'фильтр|свеч|комплект.{0,20}то') |
        Q(category__name__iregex=r'фильтр|свеч')
    ).select_related('seller_profile', 'category', 'brand', 'car_model').order_by('-pk')
    products = attach_sellers_to_products(public_wholesale_prefetch(products)[:8])
    attach_public_wholesale_flags(products)
    return render(request, 'catalog/interface/consumables.html', {'products': products})


def services_landing(request):
    """Only real active organizations, without fabricated ratings or profiles."""
    from service_requests.models import ServiceSeller
    sellers = ServiceSeller.objects.filter(is_active=True, is_test_seller=False).prefetch_related(
        'services'
    ).order_by('name', 'pk')[:4]
    return render(request, 'catalog/interface/services_landing.html', {
        'service_sellers': sellers,
    })


def parts_entry(request):
    """Keep all existing parameterized form/result URLs and POST handling intact."""
    if request.GET or request.method != 'GET':
        from .views import catalog_list
        return catalog_list(request)
    return organization_entry(request, 'parts')


def organization_entry(request, kind):
    pages = {
        'parts': {
            'title': 'Найти запчасть', 'subtitle': 'Отправьте заявку или выберите продавца самостоятельно',
            'request_title': 'Отправить заявку',
            'request_text': 'Укажите автомобиль и нужную запчасть. Заявка поступит подходящим продавцам.',
            'request_button': 'Найти запчасть', 'request_url': reverse('interface_find_part') + '?request=1',
            'catalog_title': 'Каталог продавцов',
            'catalog_text': 'Выберите магазин по городу и специализации. Посмотрите контакты и свяжитесь напрямую.',
            'catalog_url': reverse('parts_sellers_catalog_public'),
            'request_icon': 'file', 'catalog_icon': 'store',
            'cross_text': 'Нужна запчасть с авторазбора?', 'cross_label': 'Перейти к авторазборам',
            'cross_url': reverse('interface_section', kwargs={'section': 'dismantle'}),
        },
        'repair': {
            'title': 'СТО и ремонт', 'subtitle': 'Оставьте заявку или выберите СТО самостоятельно',
            'request_title': 'Заявка на ремонт',
            'request_text': 'Укажите автомобиль и опишите проблему. Заявка поступит подходящим СТО.',
            'request_button': 'Оставить заявку', 'request_url': '/service-request/?type=sto',
            'catalog_title': 'Каталог СТО',
            'catalog_text': 'Выберите станцию по городу и видам услуг. Посмотрите контакты и свяжитесь напрямую.',
            'catalog_url': reverse('services_catalog') + '?type=sto',
            'request_icon': 'tool', 'catalog_icon': 'garage',
            'cross_text': 'Нужен уход за автомобилем?', 'cross_label': 'Детейлинг и тюнинг',
            'cross_url': reverse('interface_section', kwargs={'section': 'detailing'}),
        },
        'detailing': {
            'title': 'Детейлинг и тюнинг', 'subtitle': 'Найдите специалиста по уходу и доработке автомобиля',
            'request_title': 'Заявка на услугу',
            'request_text': 'Опишите, что хотите сделать с автомобилем. Заявка поступит профильным исполнителям.',
            'request_button': 'Оставить заявку', 'request_url': '/service-request/?type=detailing',
            'catalog_title': 'Каталог исполнителей',
            'catalog_text': 'Выберите организацию по городу и услугам. Посмотрите контакты и свяжитесь напрямую.',
            'catalog_url': reverse('services_catalog') + '?type=detailing',
            'request_icon': 'car', 'catalog_icon': 'store',
            'cross_text': 'Нужен ремонт автомобиля?', 'cross_label': 'СТО и ремонт',
            'cross_url': reverse('interface_section', kwargs={'section': 'repair'}),
        },
    }
    return render(request, 'catalog/interface/organization_entry.html', {
        'entry': pages[kind], 'section_title': pages[kind]['title'], 'hide_product_search': True,
    })
