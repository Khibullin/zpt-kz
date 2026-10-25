"""Two-way request workflow. Does not enqueue or send any notifications."""
from urllib.parse import urlsplit
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Avg, Case, When, Value, BooleanField, Q
from django.core.cache import cache
from hashlib import sha256
from collections import defaultdict
from types import SimpleNamespace
import json
from django.utils import timezone

from core.models import Match, RequestInteraction, RequestOffer, Seller
from core.phone_utils import seller_whatsapp_url
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


def can_start_buyer_contact(req, seller):
    """A contact click can create participation; no separate interest step."""
    return bool(seller_can_participate(seller) and seller.contact_access_verified
                and req.contact_consent_at and not req.access_revoked
                and req.seller_feed_enabled and safe_summary(req)
                and req.purchase_result not in ('purchased', 'closed'))


def buyer_message(req):
    vehicle = ' '.join(str(value) for value in (req.brand, req.model, req.year) if value)
    return f'Здравствуйте! По вашей заявке ZPT.KZ №{req.pk}: {req.public_summary}. Автомобиль: {vehicle}. '


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
        return seller_whatsapp_url(seller, f'Здравствуйте! По заявке ZPT.KZ №{req.pk}: {req.brand} {req.model}. {req.public_summary}')
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


def seller_specialization(seller):
    """Present stored specialization without deriving promises from a shop name."""
    def distinct(values):
        result, seen = [], set()
        for value in values:
            value = ' '.join((value or '').split())
            if value and value.casefold() not in seen:
                result.append(value)
                seen.add(value.casefold())
        return result
    brands = distinct([seller.brand, seller.brand_fk.name if seller.brand_fk_id else '',
                       *[brand.name for brand in seller.selected_brands.all()]])
    categories = distinct([seller.category, *[category.name for category in seller.selected_categories.all()]])
    if brands:
        return 'Запчасти для ' + ', '.join(brands)
    if categories:
        return ', '.join(categories)
    return 'Специализацию уточните у продавца'


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


def _brand_term_index(terms):
    index = defaultdict(list)
    for term, brand in terms.items():
        words = tuple(term.split())
        if words:
            index[words[0]].append((words, brand))
    return index


def _named_brands(name, index):
    words = tuple(_brand_words(name).split())
    return {brand for offset, word in enumerate(words)
            for term, brand in index.get(word, ())
            if words[offset:offset + len(term)] == term}


def _directory_brand_specific(seller, wanted, terms, index):
    named = _named_brands(seller_display_name(seller), index)
    specific = [seller.brand, seller.brand_fk.name if seller.brand_fk_id else '',
                *[b.name for b in seller.selected_brands.all()]]
    explicit = {terms.get(_brand_words(name), _brand_words(name)) for name in specific if name}
    # Imported all_brands must not override a clearly different brand in the title.
    if named and wanted not in named and wanted not in explicit:
        return None
    return wanted in named or wanted in explicit


def _directory_sort_key(seller, specific_brand, req):
    completeness = sum(bool(value) for value in (seller.directory_description, seller.directory_photo,
                       seller.brand or seller.all_brands, seller.category or seller.all_categories))
    return not specific_brand, seller.city != req.city, -completeness, seller.pk


def _directory_queryset(req, excluded_ids=(), seller_ids=None):
    from core.directory_seo import public_parts_organizations
    shops = public_parts_organizations().exclude(pk__in=excluded_ids)
    if seller_ids is not None:
        shops = shops.filter(pk__in=seller_ids)
    if req.search_scope == 'city':
        shops = shops.filter(city=req.city)
    elif req.search_scope == 'custom':
        shops = shops.filter(city__in=[x.strip() for x in req.selected_cities.split(',')])
    if req.is_dismantler_request:
        shops = shops.filter(is_dismantler=True)
    else:
        shops = shops.filter(sells_new_parts=True)
    return shops


def directory_suggestions(req, excluded_ids=(), *, seller_ids=None):
    """Recheck current public eligibility before rendering or a contact action."""
    shops = _directory_queryset(req, excluded_ids, seller_ids).select_related(
        'brand_fk', 'model_fk').prefetch_related(
        'selected_brands', 'selected_models', 'selected_categories', 'social_links')
    terms = _directory_brand_terms()
    term_index = _brand_term_index(terms)
    wanted = terms.get(_brand_words(req.brand), _brand_words(req.brand))
    items = []
    for seller in shops:
        if not relevant(seller, req):
            continue
        name = seller_display_name(seller)
        brand_specific = _directory_brand_specific(seller, wanted, terms, term_index)
        if brand_specific is None:
            continue
        items.append({'seller': seller, 'display_name': name, 'directory_only': True,
                      'brand_specific': brand_specific,
                      'has_whatsapp': bool(directory_contact(seller, req, 'whatsapp')),
                      'has_telegram': bool(directory_contact(seller, req, 'telegram')),
                      'reason': 'Магазин из каталога'})
    items.sort(key=lambda item: _directory_sort_key(item['seller'], item['brand_specific'], req))
    return items


class _ProfileNames:
    def __init__(self, names=()):
        self.names = tuple(SimpleNamespace(name=name) for name in names)

    def all(self):
        return self.names


def _directory_order(req, excluded_ids):
    """Rank lightweight public profiles, without loading photos, texts or contacts.

    The same relevant() predicate is used for these profiles and live Seller rows.
    """
    shops = _directory_queryset(req, excluded_ids)
    relations = {}
    for field_name in ('selected_brands', 'selected_models', 'selected_categories'):
        field = Seller._meta.get_field(field_name)
        target = field.m2m_reverse_field_name()
        rows = field.remote_field.through.objects.filter(seller_id__in=shops.values('pk'))
        by_seller = defaultdict(list)
        for pk, name in rows.values_list('seller_id', target + '__name'):
            by_seller[pk].append(name)
        relations[field_name] = {pk: _ProfileNames(names) for pk, names in by_seller.items()}
    empty = _ProfileNames()
    rows = shops.annotate(has_description=Case(
        When(directory_description='', then=Value(False)), default=Value(True), output_field=BooleanField()
    )).values('pk', 'name', 'city', 'transport_type', 'sells_new_parts', 'is_dismantler', 'request_filters',
              'all_brands', 'all_models', 'all_categories', 'brand', 'model', 'category',
              'brand_fk_id', 'brand_fk__name', 'model_fk_id', 'model_fk__name',
              'directory_photo', 'has_description')
    terms = _directory_brand_terms()
    index = _brand_term_index(terms)
    wanted = terms.get(_brand_words(req.brand), _brand_words(req.brand))
    ranked = []
    for row in rows:
        profile = SimpleNamespace(**row)
        profile.brand_fk = SimpleNamespace(name=row['brand_fk__name'])
        profile.model_fk = SimpleNamespace(name=row['model_fk__name'])
        for name, mapping in relations.items():
            setattr(profile, name, mapping.get(profile.pk, empty))
        if not relevant(profile, req):
            continue
        specific_brand = _directory_brand_specific(profile, wanted, terms, index)
        if specific_brand is None:
            continue
        profile.directory_description = row['has_description']
        ranked.append((_directory_sort_key(profile, specific_brand, req), profile.pk))
    ranked.sort()
    return [pk for _key, pk in ranked]


def decorate_buyer_seller_cards(items):
    """Hydrate display facts in two bounded queries, independent of routing."""
    from core.models import SellerExternalCard, RequestSellerReview
    from core.services.seller_card_links import validate_card_url
    from core.services.seller_card_display import route_links
    ids = [item['seller'].pk for item in items]
    stats = {row['seller_id']: row for row in RequestSellerReview.objects.filter(
        seller_id__in=ids, public_consent=True, moderated=True,
        request__purchase_result='purchased').values('seller_id')
        .annotate(review_count=Count('pk'), review_rating=Avg('rating'))}
    maps = {}
    for card in SellerExternalCard.objects.filter(seller_id__in=ids, source='two_gis'):
        url, error = validate_card_url('two_gis', card.card_url)
        if url and not error:
            maps.setdefault(card.seller_id, url)
    for item in items:
        seller = item['seller']
        routes = route_links(seller.latitude, seller.longitude)
        item['two_gis_url'] = maps.get(seller.pk) or (routes[0]['url'] if routes else '')
        item.update(stats.get(seller.pk, {'review_count': 0, 'review_rating': None}))
    return items


def buyer_seller_page(req, number):
    """Cache public directory ordering only; hydrate and recheck every displayed shop.

    Request access, direct opt-ins, offers and prices are never cached here.
    Actions always resolve their own current eligibility independently.
    """
    from django.core.paginator import Paginator
    direct = recommendations(req)
    excluded = [item['seller'].pk for item in direct]
    signature = (str(req.access_token), req.pk, req.brand, req.model, req.category,
                 req.transport_type, req.city, req.search_scope, req.selected_cities,
                 req.is_dismantler_request, sorted(excluded))
    key = 'buyer-directory-order:v1:' + sha256(json.dumps(signature).encode()).hexdigest()
    ids = cache.get(key)
    if ids is None:
        ids = _directory_order(req, excluded)
        cache.set(key, ids, timeout=60)
    page = Paginator(direct + [{'directory_id': pk} for pk in ids], 10).get_page(number)
    wanted = [item['directory_id'] for item in page if 'directory_id' in item]
    current = {item['seller'].pk: item for item in directory_suggestions(req, seller_ids=wanted)} if wanted else {}
    page.object_list = [current[item['directory_id']] if 'directory_id' in item else item
                        for item in page if 'directory_id' not in item or item['directory_id'] in current]
    for item in page:
        item['specialization'] = seller_specialization(item['seller'])
    decorate_buyer_seller_cards(page.object_list)
    return page


def record_impressions(req, page):
    # One bounded INSERT instead of up to thirty round trips per page.
    RequestInteraction.objects.bulk_create([
        RequestInteraction(request=req, seller=item['seller'], actor='buyer', kind='impression',
                           source='site', position=index)
        for index, item in enumerate(page, start=page.start_index())
    ], ignore_conflicts=True)


def directory_contact(seller, req, channel):
    """Public business contacts only; never grants a seller access to buyer data."""
    if channel == 'whatsapp':
        return seller_whatsapp_url(seller,
            f'Здравствуйте! По заявке ZPT.KZ №{req.pk}: {req.brand} {req.model}. {getattr(req, "public_summary", "") or req.category}. Уточните наличие и цену.')
    if channel == 'telegram':
        for link in seller.social_links.all():
            if link.kind == 'telegram' and (target := telegram_contact(link.url)):
                return target
    if channel == 'call':
        from core.phone_utils import normalize_phone_for_whatsapp
        digits = normalize_phone_for_whatsapp(seller.whatsapp) or normalize_phone_for_whatsapp(seller.phone2)
        return 'tel:+' + digits if digits else ''
    return ''


def response_metrics(sellers):
    """One mature, relevant, confirmed delivery per request, across both channels.

    Telegram acceptance/manual WhatsApp confirmation is not proof of reading.
    Opening alone is not a response. New sellers start with a neutral prior.
    """
    from datetime import timedelta
    from fractions import Fraction
    from core.models import RequestSellerNotification
    sellers = {seller.pk: seller for seller in sellers}
    metrics = {pk: {'received': 0, 'responded': 0, 'score': Fraction(1, 2)} for pk in sellers}
    if not sellers:
        return metrics
    cutoff = timezone.now() - timedelta(hours=72)
    notices = RequestSellerNotification.objects.filter(
        seller_id__in=sellers, updated_at__lte=cutoff,
        request__access_revoked=False,
    ).filter(Q(channel='telegram', status='sent', message_id__gt=0) |
             Q(channel='whatsapp', status='manual')).select_related('request')
    pairs = set()
    for notice in notices:
        if relevant(sellers[notice.seller_id], notice.request):
            pairs.add((notice.seller_id, notice.request_id))
    responses = set(RequestInteraction.objects.filter(
        seller_id__in=sellers, request_id__in={request_id for _, request_id in pairs}, actor='seller',
        kind__in=('interest', 'out_of_stock', 'cannot_fulfill', 'contact_confirmed', 'supply_confirmed'),
    ).values_list('seller_id', 'request_id')) if pairs else set()
    for seller_id, request_id in pairs:
        metrics[seller_id]['received'] += 1
        metrics[seller_id]['responded'] += (seller_id, request_id) in responses
    for item in metrics.values():
        item['score'] = Fraction(item['responded'] + 1, item['received'] + 2)
    return metrics


def recommendations(req):
    sellers = Seller.objects.filter(is_active=True, is_paused=False, direct_contacts_enabled=True).select_related(
        'brand_fk', 'model_fk').prefetch_related('selected_brands', 'selected_models', 'selected_categories', 'social_links')
    offers = {o.seller_id: o for o in RequestOffer.objects.filter(request=req, expires_at__gt=timezone.now(), fitment_confirmed=True)}
    purchases = dict(RequestInteraction.objects.filter(kind='purchase_confirmed', actor='buyer', request__purchase_result='purchased').values('seller_id').annotate(n=Count('id')).values_list('seller_id', 'n'))
    from core.models import RequestSellerReview
    reviews = dict(RequestSellerReview.objects.filter(moderated=True, public_consent=True, request__purchase_result='purchased').values('seller_id').annotate(score=Avg('rating')).values_list('seller_id', 'score'))
    sellers = list(sellers)
    responses = response_metrics(sellers)
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
                                -responses[seller.pk]['score'],
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
