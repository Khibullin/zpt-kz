"""Two-way request workflow. Does not enqueue or send any notifications."""
from urllib.parse import urlsplit
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Avg
from django.utils import timezone

from core.models import Match, RequestInteraction, RequestOffer, Seller
from core.phone_utils import build_whatsapp_url
from core.services.seller_request_access import get_or_create_active_seller_request_access
from core.services.seller_request_filters import matches_request

PII = re.compile(r'(?:\+?\d[\d\s().-]{8,}\d|\b[A-HJ-NPR-Z0-9]{17}\b|@|https?://|www\.)', re.I)


def enabled():
    return bool(getattr(settings, 'TWO_WAY_REQUESTS_ENABLED', False))


def safe_summary(req):
    # No raw description, VIN, phone, photos or access tokens in a feed.
    parts = [f'Заявка №{req.pk}', req.brand, req.model, str(req.year or ''), req.city, req.public_summary]
    if not req.public_summary.strip() or any(PII.search(str(x or '')) for x in parts):
        return ''
    return ' · '.join(str(x).strip() for x in parts if str(x or '').strip())


def buyer_access_valid(req):
    return not req.access_revoked and (req.access_expires_at is None or req.access_expires_at > timezone.now())


def seller_can_participate(seller):
    return bool(seller and seller.user_id and seller.is_active and not seller.is_paused and not seller.catalog_only)


def can_open_contacts(req, seller):
    return bool(seller_can_participate(seller) and seller.contact_access_verified
                and req.contact_consent_at and not req.access_revoked
                and req.purchase_result not in ('purchased', 'closed')
                and RequestInteraction.objects.filter(request=req, seller=seller, actor='seller', kind='interest').exists()
                and req.seller_access_links.filter(seller=seller, expires_at__gt=timezone.now()).exists())


def record(req, seller, actor, kind, source='site', position=None):
    if actor not in {'seller', 'buyer'} or kind not in dict(RequestInteraction._meta.get_field('kind').choices):
        raise ValueError('Неизвестное действие.')
    return RequestInteraction.objects.get_or_create(
        request=req, seller=seller, actor=actor, kind=kind,
        defaults={'source': source, 'position': position})[0]


def participate(req, seller, source='site'):
    if not seller_can_participate(seller) or not req.seller_feed_enabled or not safe_summary(req):
        raise ValueError('Отклик недоступен.')
    if req.purchase_result in ('purchased', 'closed'):
        raise ValueError('Заявка закрыта.')
    with transaction.atomic():
        # Stable row lock serializes the first access/Match creation too.
        Seller.objects.select_for_update().get(pk=seller.pk)
        match, _ = Match.objects.get_or_create(request=req, seller=seller)
        record(req, seller, 'seller', 'interest', source)
        access = get_or_create_active_seller_request_access(request=req, seller=seller)
    return access


def telegram_contact(raw):
    """Only a direct username chat, never a group, invite, bot or arbitrary URL."""
    value = str(raw or '').strip()
    if value.startswith('https://'):
        parsed = urlsplit(value)
        if parsed.hostname not in ('t.me', 'telegram.me') or parsed.query or parsed.fragment or parsed.username or parsed.netloc not in ('t.me', 'telegram.me'):
            return ''
        value = parsed.path.strip('/')
    else:
        value = value.lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', value) or value.lower().endswith('bot'):
        return ''
    return 'https://t.me/' + value


def seller_contact(seller, req, channel):
    if not seller.is_active or seller.is_paused or not seller.direct_contacts_enabled:
        return ''
    if channel == 'whatsapp':
        return build_whatsapp_url(seller.whatsapp, f'Здравствуйте! По заявке ZPT.KZ №{req.pk}: {req.brand} {req.model}. {req.public_summary}')
    if channel == 'telegram':
        for link in seller.social_links.all():
            if link.kind != 'telegram':
                continue
            target = telegram_contact(link.url)
            if target:
                return target
    return ''


def relevant(seller, req):
    if req.search_scope == 'city' and seller.city != req.city:
        return False
    if req.search_scope == 'custom' and seller.city not in [x.strip() for x in req.selected_cities.split(',')]:
        return False
    if req.is_dismantler_request and not seller.is_dismantler:
        return False
    if not req.is_dismantler_request and seller.sells_new_parts is not True:
        return False
    if seller.request_filters is not None:
        return matches_request(seller.request_filters, req)
    if seller.transport_type and seller.transport_type != req.transport_type:
        return False
    # Empty profile is allowed to respond, but not claimed to be relevant.
    brands = {x.name.casefold() for x in seller.selected_brands.all()}
    if seller.brand:
        brands.add(seller.brand.casefold())
    if seller.brand_fk_id:
        brands.add(seller.brand_fk.name.casefold())
    known_models = {x.name.casefold() for x in seller.selected_models.all()}
    if seller.model:
        known_models.add(seller.model.casefold())
    if seller.model_fk_id:
        known_models.add(seller.model_fk.name.casefold())
    if known_models and not seller.all_models and req.model.casefold() not in known_models:
        return False
    categories = {x.name.casefold() for x in seller.selected_categories.all()}
    if seller.category:
        categories.add(seller.category.casefold())
    return bool((seller.all_brands or req.brand.casefold() in brands)
                and (seller.all_categories or req.category.casefold() in categories))


def seller_display_name(seller):
    name = re.sub(r'\s*[•|]\s*Instagram\b.*$', '', seller.name, flags=re.I)
    name = re.sub(r'\s*\(@[A-Za-z0-9_.]+\)', '', name)
    return ' '.join(name.split())


def _brand_words(value):
    return ' '.join(re.findall(r'\w+', (value or '').casefold()))


def _directory_brand_terms():
    from core.models import Brand
    aliases = {
        'toyota': ('тойота', 'тоёта'), 'subaru': ('субару',),
        'volkswagen': ('фольксваген', 'фольксваген ваг'),
        'chevrolet': ('шевроле', 'шевролет'), 'hyundai': ('хендай', 'хундай', 'хёндэ'),
        'kia': ('киа',), 'mercedes benz': ('mercedes', 'мерседес', 'мерседес бенц'),
        'bmw': ('бмв',), 'nissan': ('ниссан',), 'honda': ('хонда',),
        'mazda': ('мазда',), 'mitsubishi': ('митсубиси', 'мицубиси'),
        'lexus': ('лексус',), 'audi': ('ауди',), 'ford': ('форд',),
        'renault': ('рено',), 'lada': ('лада', 'ваз'), 'suzuki': ('сузуки',),
    }
    terms = {_brand_words(name): _brand_words(name) for name in Brand.objects.values_list('name', flat=True)
             if len(_brand_words(name)) >= 3}
    for canonical, alternatives in aliases.items():
        for term in (canonical, *alternatives):
            terms[_brand_words(term)] = canonical
    return terms


def directory_suggestions(req, excluded_ids=()):
    """Suggest already public shops without asserting a contact opt-in or stock."""
    from core.directory_seo import public_parts_organizations
    shops = public_parts_organizations().exclude(pk__in=excluded_ids).select_related(
        'brand_fk', 'model_fk').prefetch_related('selected_brands', 'selected_models', 'selected_categories', 'social_links')
    if req.search_scope == 'city':
        shops = shops.filter(city=req.city)
    elif req.search_scope == 'custom':
        shops = shops.filter(city__in=[x.strip() for x in req.selected_cities.split(',')])
    if req.is_dismantler_request:
        shops = shops.filter(is_dismantler=True)
    else:
        shops = shops.filter(sells_new_parts=True)
    terms = _directory_brand_terms()
    wanted = terms.get(_brand_words(req.brand), _brand_words(req.brand))
    items = []
    for seller in shops:
        if not relevant(seller, req):
            continue
        name = seller_display_name(seller)
        words = ' ' + _brand_words(name) + ' '
        named_brands = {brand for term, brand in terms.items() if ' ' + term + ' ' in words}
        specific = [seller.brand, seller.brand_fk.name if seller.brand_fk_id else '',
                    *[b.name for b in seller.selected_brands.all()]]
        explicit = {terms.get(_brand_words(b), _brand_words(b)) for b in specific if b}
        # Imported all_brands=True must not override a clearly different brand in the shop title.
        if named_brands and wanted not in named_brands and wanted not in explicit:
            continue
        items.append({'seller': seller, 'display_name': name, 'directory_only': True,
                      'brand_specific': wanted in named_brands or wanted in explicit,
                      'has_whatsapp': bool(directory_contact(seller, req, 'whatsapp')),
                      'has_telegram': bool(directory_contact(seller, req, 'telegram')),
                      'reason': 'Магазин из каталога'})
    items.sort(key=lambda x: (not x['brand_specific'], x['seller'].city != req.city,
        -sum(bool(v) for v in (x['seller'].directory_description, x['seller'].directory_photo,
                               x['seller'].brand or x['seller'].all_brands,
                               x['seller'].category or x['seller'].all_categories)), x['seller'].pk))
    return items


def directory_contact(seller, req, channel):
    """Public business contacts only; never grants a seller access to buyer data."""
    if channel == 'whatsapp':
        return build_whatsapp_url(seller.whatsapp,
            f'Здравствуйте! По заявке ZPT.KZ №{req.pk}: {req.brand} {req.model}. Категория: {req.category}. Уточните наличие и цену.')
    if channel == 'telegram':
        for link in seller.social_links.all():
            if link.kind == 'telegram' and (target := telegram_contact(link.url)):
                return target
    if channel == 'call':
        from core.phone_utils import normalize_phone_for_whatsapp
        digits = normalize_phone_for_whatsapp(seller.whatsapp) or normalize_phone_for_whatsapp(seller.phone2)
        return 'tel:+' + digits if digits else ''
    return ''


def recommendations(req):
    sellers = Seller.objects.filter(is_active=True, is_paused=False, direct_contacts_enabled=True).select_related(
        'brand_fk', 'model_fk').prefetch_related('selected_brands', 'selected_models', 'selected_categories', 'social_links')
    offers = {o.seller_id: o for o in RequestOffer.objects.filter(request=req, expires_at__gt=timezone.now(), fitment_confirmed=True)}
    purchases = dict(RequestInteraction.objects.filter(kind='purchase_confirmed', actor='buyer', request__purchase_result='purchased').values('seller_id').annotate(n=Count('id')).values_list('seller_id', 'n'))
    from core.models import RequestSellerReview
    reviews = dict(RequestSellerReview.objects.filter(moderated=True, public_consent=True, request__purchase_result='purchased').values('seller_id').annotate(score=Avg('rating')).values_list('seller_id', 'score'))
    result = []
    for seller in sellers:
        offer = offers.get(seller.pk)
        if not seller.contact_access_verified:
            offer = None
        if offer and ((req.requested_quantity and offer.quantity != req.requested_quantity)
                      or (req.buyer_priority == 'original' and not offer.is_original)):
            offer = None
        # An explicit offer gives an incomplete profile a chance; geography remains strict.
        in_geo = (req.search_scope not in ('city', 'custom') or
                  (req.search_scope == 'city' and seller.city == req.city) or
                  (req.search_scope == 'custom' and seller.city in [x.strip() for x in req.selected_cities.split(',')]))
        if not in_geo or (offer is None and not relevant(seller, req)):
            continue
        if offer and ((req.is_dismantler_request and offer.condition != 'used') or
                      (not req.is_dismantler_request and offer.condition != 'new')):
            offer = None
            if not relevant(seller, req):
                continue
        total = offer.price + offer.delivery_price if offer and offer.delivery_price is not None else None
        if total is not None and req.buyer_budget and total > req.buyer_budget:
            offer, total = None, None
            if not relevant(seller, req):
                continue
        # Without requested quantity, totals of different sets are not comparable.
        comparable = total if req.requested_quantity else None
        price_order = (comparable is None, comparable or 0)
        time_order = (offer.delivery_days if offer else 9999,)
        value_order = time_order + price_order if req.buyer_priority == 'urgent' else price_order + time_order
        result.append({'seller': seller, 'display_name': seller_display_name(seller), 'offer': offer, 'total_price': total,
                       'has_whatsapp': bool(seller_contact(seller, req, 'whatsapp')),
                       'has_telegram': bool(seller_contact(seller, req, 'telegram')),
                       'reason': ('Продавец подтвердил применимость; условия указаны ниже' if offer else 'Профиль соответствует запросу; наличие уточняется'),
                       '_key': (offer is None, seller.notification_state != 'active', *value_order,
                                -purchases.get(seller.pk, 0), -reviews.get(seller.pk, 0),
                                -sum(bool(x) for x in (seller.directory_description, seller.directory_photo, seller.city, seller.brand or seller.all_brands, seller.category or seller.all_categories)), seller.pk)})
    result.sort(key=lambda x: x['_key'])
    return result


def result_label(req):
    labels = {'purchased': 'Покупка подтверждена', 'closed': 'Закрыта без покупки', 'search_again': 'Требуется повторный поиск'}
    if req.purchase_result in labels:
        return labels[req.purchase_result]
    events = set(req.interactions.values_list('kind', flat=True))
    if 'supply_confirmed' in events and 'contact_confirmed' in events:
        return 'Согласование покупки'
    if 'contact_confirmed' in events:
        return 'Контакт подтверждён'
    if events & {'whatsapp_click', 'telegram_click', 'call_click'}:
        return 'Контакт инициирован'
    if 'interest' in events:
        return 'Продавцы заинтересовались'
    return 'Поиск продавцов' if req.seller_feed_enabled else 'На проверке'
