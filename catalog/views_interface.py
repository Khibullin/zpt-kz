"""Navigation for the approved homepage; existing transactional views stay authoritative."""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse


def homepage_tiles():
    section = lambda name: reverse('interface_section', kwargs={'section': name})
    return [
        {'key': 'parts', 'url': reverse('interface_find_part'), 'art': 'parts', 'caption': 'partsHint'},
        {'key': 'dismantle', 'url': section('dismantle'), 'title': 'dismantleShort', 'art': 'dismantle', 'caption': 'dismantleHint'},
        {'key': 'consumables', 'url': reverse('consumables_landing'), 'art': 'kits', 'caption': 'consumablesHint'},
        {'key': 'serviceHub', 'url': reverse('services_landing'), 'art': 'repair', 'caption': 'serviceHubHint'},
        {'key': 'wholesale', 'url': reverse('public_seller_wholesale', kwargs={'slug':'ag-parts'}), 'art': 'wholesale', 'caption': 'wholesaleHint'},
    ]


def interface_section(request, section):
    if section in ('repair', 'detailing'):
        from service_requests.catalog_pages import service_directory
        return service_directory(request, section=section)
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
