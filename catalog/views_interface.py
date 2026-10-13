"""Navigation for the approved homepage; existing transactional views stay authoritative."""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse


def homepage_tiles():
    section = lambda name: reverse('interface_section', kwargs={'section': name})
    return [
        {'key': 'parts', 'url': reverse('interface_find_part')},
        {'key': 'dismantle', 'url': section('dismantle')},
        {'key': 'shop', 'url': '/?all=1'},
        {'key': 'ag', 'url': reverse('ag_parts_filter_finder')},
        {'key': 'kits', 'url': reverse('maintenance_kit_list')},
        {'key': 'repair', 'url': section('repair')},
        {'key': 'detailing', 'url': section('detailing')},
        {'key': 'wholesale', 'url': reverse('public_seller_wholesale', kwargs={'slug': 'ag-parts'})},
    ]


def interface_section(request, section):
    parts = reverse('interface_find_part')
    used = parts + '?condition=used'
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
