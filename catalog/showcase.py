"""Hourly homepage variety with session-pinned, non-overlapping pagination.

Only public active Product IDs are saved in the session. Prices, stock and
purchase links are read afresh for each page. No stock or commerce mutation.
"""
from collections import Counter, defaultdict, deque
from hashlib import blake2b

from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.utils import timezone

from catalog.models import ProductKaspiListing
from catalog.product_display import CJK, public_product_title
from catalog.templatetags.kaspi_buy import kaspi_buy_url
from catalog.wholesale import public_stock_status
from core.phone_utils import normalize_phone_for_whatsapp

PAGE_SIZE = 12
SESSION_KEY = 'homepage_showcase_v1'
# Keeps independently opened tabs stable across the hourly rotation.
MAX_SNAPSHOTS = 24

CATEGORY_WORDS = (
    ('filters', ('фильтр', 'filter')),
    ('spark', ('свеч', 'spark plug')),
    ('brakes', ('колодк', 'суппорт', 'тормозн', 'brake')),
    ('steering', ('рулев', 'тяга панара', 'наконечник', 'steering')),
    ('suspension', ('амортиз', 'пружин', 'рычаг', 'шаровая', 'сайлентблок',
                    'ступиц', 'подшипник', 'лифт комплект', 'shock', 'suspension')),
    ('engine', ('порш', 'форсунк', 'двигател', 'piston', 'injector')),
    ('body', ('бампер', 'двер', 'молдинг', 'крыло', 'капот', 'bumper')),
    ('lights', ('фара', 'фонарь', 'оптика', 'headlight')),
    ('transmission', ('шрус', 'гидромуфт', 'трансмис', 'сцеплен')),
)


def seller_key(product):
    # The caller attaches sellers using the existing public seller resolver.
    seller = getattr(product, 'seller', None)
    if seller is not None:
        return f'profile:{seller.pk}'
    phone = normalize_phone_for_whatsapp(product.whatsapp_number)
    if phone:
        return f'phone:{phone}'
    name = product.seller_name.strip().casefold()
    return f'name:{name}' if name else 'unknown'


def category_key(product):
    title = public_product_title(product).casefold().replace('ё', 'е')
    for key, words in CATEGORY_WORDS:
        if any(word in title for word in words):
            return key
    return f'category:{product.category_id}' if product.category_id else 'other'


def card_quality(product):
    """Presentation/orderability signals, never a claim of live Kaspi stock."""
    title = public_product_title(product)
    priced = product.price is not None and product.price > 0 and not product.price_on_request
    kaspi = bool(kaspi_buy_url(product))
    direct_cart = (
        not product.seller_profile or product.seller_profile.slug != 'ag-parts'
    ) and public_stock_status(product)['can_buy']
    return (3 * bool(product.main_image) + 2 * bool(title and not CJK.search(title))
            + 2 * priced + 2 * bool(priced and (kaspi or direct_cart)))


def _rotation_value(hour, product_id):
    digest = blake2b(f'{hour}:{product_id}'.encode(), digest_size=8).digest()
    return int.from_bytes(digest, 'big') / (2 ** 64)


def build_showcase_order(products, hour):
    """Balance sellers/categories; include both latest and older stock listings.

    Within each 12-card block, two per seller is a hard cap while alternatives
    remain. Adjacent sellers/categories differ whenever the eligible pool permits.
    A bounded hourly tie-break lets older well-presented products rotate in.
    """
    products = list(products)
    latest = sorted(products, key=lambda p: (p.created_at, p.pk), reverse=True)
    recent_ids = {p.pk for p in latest[:max(1, len(latest) // 4)]}
    buckets = defaultdict(list)
    for product in products:
        seller, category = seller_key(product), category_key(product)
        recent = product.pk in recent_ids
        rank = card_quality(product) + 6 * _rotation_value(hour, product.pk)
        buckets[(seller, category, recent)].append((rank, product.pk))
    queues = {key: deque(sorted(items, reverse=True)) for key, items in buckets.items()}
    ordered = []
    last_seller = last_category = None
    sellers, categories = Counter(), Counter()
    while queues:
        if len(ordered) % PAGE_SIZE == 0:
            sellers, categories = Counter(), Counter()
        options = list(queues)
        capped = [key for key in options if sellers[key[0]] < 2]
        options = capped or options
        different = [key for key in options if key[0] != last_seller]
        options = different or options
        # Every third slot gives extra weight to the latest quarter; the others
        # give it to earlier listings. Diversity and quality remain meaningful.
        want_recent = len(ordered) % 3 == 0
        key = min(options, key=lambda key: (
            key[1] == last_category,
            categories[key[1]],
            sellers[key[0]],
            -(queues[key][0][0] + 3 * (key[2] == want_recent)),
            key,
        ))
        seller, category, _ = key
        _, product_id = queues[key].popleft()
        ordered.append(product_id)
        if not queues[key]:
            del queues[key]
        sellers[seller] += 1
        categories[category] += 1
        last_seller, last_category = seller, category
    return ordered


def homepage_showcase_page(request, queryset, attach_sellers):
    hour = str(int(timezone.now().timestamp() // 3600))
    snapshots = request.session.get(SESSION_KEY, {})
    requested = request.GET.get('showcase', '')
    snapshot_key = requested if requested in snapshots else hour
    if snapshot_key not in snapshots:
        # Avoid fetching fitments and wholesale details for the entire catalogue.
        candidates = queryset.prefetch_related(None).select_related(None).select_related(
            'seller_profile'
        ).only(
            'pk', 'title', 'main_image', 'price', 'price_on_request', 'stock_qty',
            'created_at', 'category_id', 'seller_profile_id', 'seller_name',
            'whatsapp_number', 'seller_profile__id', 'seller_profile__slug',
        ).prefetch_related(Prefetch('kaspi_listings', queryset=ProductKaspiListing.objects.only(
            'product_id', 'is_active', 'public_url'
        )))
        ids = build_showcase_order(attach_sellers(candidates), int(hour))
        snapshots = {**snapshots, hour: ids}
        snapshots = dict(sorted(snapshots.items(), reverse=True)[:MAX_SNAPSHOTS])
        request.session[SESSION_KEY] = snapshots
    page = Paginator(snapshots[snapshot_key], PAGE_SIZE).get_page(
        request.GET.get('showcase_page', 1)
    )
    # Slice the immutable ID sequence BEFORE filtering withdrawn products, so
    # deleting/hiding a listing cannot shift another item across page boundaries.
    ids = list(page.object_list)
    current = {p.pk: p for p in queryset.filter(pk__in=ids)}
    page.object_list = [current[pk] for pk in ids if pk in current]
    return page, snapshot_key
