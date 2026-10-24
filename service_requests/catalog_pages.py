"""Public directory presentation; does not participate in request dispatch."""
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from core.kazakhstan_locations import canonical_kazakhstan_city
from core.phone_utils import seller_whatsapp_url
from .models import ServiceSeller

REPAIR = [
    ('diagnostics', 'Диагностика', ('диагност',)),
    ('engine', 'Двигатель и КПП', ('двигател', 'акпп', 'кпп', 'трансмис', 'мотор')),
    ('brakes', 'Ходовая и тормоза', ('ходов', 'тормоз', 'подвес', 'развал')),
    ('electric', 'Автоэлектрика', ('электр', 'стартер', 'генератор')),
    ('climate', 'Кондиционеры', ('кондиц', 'климат')),
    ('tires', 'Шиномонтаж', ('шиномонт', 'балансиров')),
]
DETAIL = [
    ('polish', 'Полировка', ('полиров',)),
    ('clean', 'Химчистка', ('химчист',)),
    ('ceramic', 'Керамика', ('керамик',)),
    ('film', 'Защитная плёнка', ('антиграв', 'защитн', 'бронепл')),
    ('tint', 'Тонировка', ('тониров',)),
    ('tuning', 'Тюнинг', ('тюнинг', 'автозвук', 'шумоизоляц', 'перетяж', 'оптик')),
]

def catalog_scopes(seller, names):
    # Services are evidence of specialization. An unclassified imported profile
    # does not gain detailing membership without evidence of that specialization.
    text = ' '.join([seller.name, seller.description, *names]).casefold()
    repair = any(word in text for _, _, words in REPAIR for word in words) or 'кузовн' in text or 'ремонт' in text
    detail = any(word in text for _, _, words in DETAIL for word in words) or 'детейлинг' in text or 'мойка' in text
    if seller.seller_type == 'detailing':
        detail = True
    if repair or detail:
        return {kind for kind, found in [('sto', repair), ('detailing', detail)] if found}
    return {'sto'}

def service_directory(request, section=None):
    kind = {'repair': 'sto', 'detailing': 'detailing'}.get(section, request.GET.get('type', ''))
    if kind not in ('sto', 'detailing'):
        kind = ''
    if kind in ('sto', 'detailing'):
        from .directory_display import repair_directory
        return repair_directory(request, kind)
    title = {'sto': 'СТО и ремонт', 'detailing': 'Детейлинг и тюнинг'}.get(kind, 'Каталог исполнителей')
    raw_city = request.GET.get('city', '').strip()
    city = canonical_kazakhstan_city(raw_city) if raw_city else ''
    q = request.GET.get('q', '').strip()[:200]
    category = request.GET.get('category', '')
    service = request.GET.get('service', '')
    district = request.GET.get('district', '').strip()[:100]
    categories = DETAIL if kind == 'detailing' else REPAIR if kind == 'sto' else REPAIR + DETAIL
    selected = next((item for item in categories if item[0] == category), None)
    sellers = ServiceSeller.objects.filter(is_active=True).prefetch_related('services').order_by('name', 'pk')
    if raw_city:
        sellers = sellers.filter(city=city) if city else sellers.none()
    if district:
        sellers = sellers.filter(district=district)
    if q:
        sellers = sellers.filter(Q(name__icontains=q) | Q(address__icontains=q) | Q(description__icontains=q) | Q(services__name__icontains=q)).distinct()
    cards = []
    service_names = set()
    for seller in sellers:
        names = [s.name for s in seller.services.all()]
        scopes = catalog_scopes(seller, names)
        if kind and kind not in scopes:
            continue
        service_names.update(names)
        if service and service not in names:
            continue
        # Narrow service filters only match known information, not unknown scope.
        evidence = ' '.join([seller.name, seller.description, *names]).casefold()
        if selected and not any(word in evidence for word in selected[2]):
            continue
        cards.append({'seller': seller, 'services': names, 'whatsapp_url': seller_whatsapp_url(seller)})
    page = Paginator(cards, 12).get_page(request.GET.get('page', 1))
    params = request.GET.copy()
    params.pop('page', None)
    if kind:
        params['type'] = kind
    for number, key in [(page.previous_page_number() if page.has_previous() else None, 'previous'), (page.next_page_number() if page.has_next() else None, 'next')]:
        if number:
            target = params.copy(); target['page'] = number
            setattr(page, key + '_url', '?' + target.urlencode() + '#service-catalog')
    tiles = []
    for key, label, words in categories:
        values = {'type': kind, 'category': key}
        if city: values['city'] = city
        tiles.append({'key': key, 'label': label, 'url': '/catalog/services/?' + urlencode(values) + '#service-catalog'})
    return render(request, 'catalog/interface/services/page.html', {
        'hide_product_search': True, 'service_title': title, 'service_kind': kind,
        'is_detailing': kind == 'detailing', 'service_tiles': tiles,
        'service_options': sorted(service_names), 'sellers': page, 'page_obj': page,
        'filters': {'q': q, 'city': raw_city, 'district': district, 'service': service, 'category': category},
        'catalog_url': '/catalog/services/?' + urlencode({'type': kind}),
        'request_url': '/service-request/?' + urlencode({'type': kind or 'sto'}),
    })
