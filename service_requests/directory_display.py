"""Presentation for the approved repair directory. No request dispatch writes."""
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from core.kazakhstan_locations import canonical_kazakhstan_city
from core.phone_utils import build_whatsapp_url, normalize_phone_for_whatsapp
from core.services.seller_card_links import clean_http_url
from core.services.seller_card_display import format_phone
from core.templatetags.dismantler_detail import clean_value
from .models import ServiceSeller


def service_card(seller):
    phone = normalize_phone_for_whatsapp(seller.whatsapp)
    return {
        'seller': seller,
        'phone': format_phone(seller.whatsapp) if phone else '',
        'tel': '+%s' % phone if phone else '',
        'whatsapp_url': build_whatsapp_url(seller.whatsapp),
        'address': clean_value(seller.address), 'district': clean_value(seller.district),
        'description': clean_value(seller.description), 'hours': clean_value(seller.working_hours),
        'services': [s.name for s in seller.services.all() if clean_value(s.name)],
        'website': clean_http_url(seller.website)[0],
        'instagram': clean_http_url(seller.instagram)[0],
        'map_url': clean_http_url(seller.map_link)[0],
    }


def repair_directory(request):
    from .catalog_pages import REPAIR, catalog_scopes
    base = ServiceSeller.objects.filter(is_active=True, is_test_seller=False, is_paused=False).prefetch_related('services').order_by('name', 'pk')
    cards = [service_card(s) for s in base if 'sto' in catalog_scopes(s, [x.name for x in s.services.all()])]
    cities = sorted({c['seller'].city for c in cards if c['seller'].city})
    services = sorted({name for c in cards for name in c['services']})
    q = request.GET.get('q', '').strip()[:200]
    raw_city = request.GET.get('city', '').strip()
    city = canonical_kazakhstan_city(raw_city) if raw_city else ''
    service = request.GET.get('service', '').strip()
    district = request.GET.get('district', '').strip()[:100]
    category = request.GET.get('category', '').strip()
    selected = next((item for item in REPAIR if item[0] == category), None)
    results = []
    for card in cards:
        seller = card['seller']
        text = ' '.join([seller.name, card['address'], card['description'], *card['services']]).casefold()
        if raw_city and (not city or seller.city != city): continue
        if q and q.casefold() not in text: continue
        if service and service not in card['services']: continue
        if district and district.casefold() not in seller.district.casefold(): continue
        if selected and not any(word in text for word in selected[2]): continue
        results.append(card)
    page = Paginator(results, 20).get_page(request.GET.get('page', 1))
    return render(request, 'catalog/interface/services/repair_catalog.html', {
        'sellers': page, 'page_obj': page, 'cities': cities, 'service_options': services,
        'categories': REPAIR, 'section_title': 'СТО и ремонт', 'hide_product_search': True,
        'filters': {'q': q, 'city': raw_city, 'service': service, 'district': district, 'category': category},
        'has_directory_filters': any((q, raw_city, service, district, category)),
    })


def repair_profile(request, seller):
    if seller.is_test_seller or seller.is_paused:
        raise Http404
    return render(request, 'catalog/interface/services/repair_detail.html', {
        'seller': seller, 'card': service_card(seller),
        'section_title': 'СТО и ремонт', 'hide_product_search': True,
    })
