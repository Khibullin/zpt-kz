from django.db.models import Q
from django.db import migrations


AG_PARTS_SLUG = 'ag-parts'

WHITELIST_ARTICLES = frozenset({
    '1017110XED95',
    '1017110XEN01',
    '13033898-00',
    '272774M400',
    '301000265AA',
    '301001199AA',
    '4801012010',
    '8100103XKV08A',
    '8100422XNZ01A',
    '8104102P3010',
    '8104400ASZ08A',
    '8104400XKY28B',
    '8104400XP24BA',
    '8114010U8520',
    '8890649934',
    'C281F2801032601',
    'CD569F2801032700',
    'D20T0120700',
    'EM2E-8121211E',
    'F4J161012030',
    'F4J163707010',
    'HU71151X',
    'RF059ZKR',
    'S111F2801031700',
    'T218107011',
    'X0390000206',
})

AIR_FILTER_ARTICLES = frozenset({
    '1109110XKV08A',
    '1109190CR01',
    '151000025AA',
    'S1010140400',
    'T151109111',
})

LEGACY_DUPLICATES = (
    {
        'legacy_article': 'F4J16-3707010',
        'canonical_article': 'F4J163707010',
    },
    {
        'legacy_article': 'D20T0120700,',
        'canonical_article': 'D20T0120700',
    },
)


def article_key(article):
    return (article or '').strip()


def normalize_article(article):
    return (article or '').strip().upper().rstrip(',').strip()


def compact_article(article):
    return ''.join(ch for ch in normalize_article(article) if ch.isalnum())


def ag_parts_products(Product, seller):
    return Product.objects.filter(
        Q(seller_profile_id=seller.pk)
        | Q(seller_profile__isnull=True, seller_name__iexact=seller.name)
    )


def strip_wholesale(product, ProductPriceTier):
    ProductPriceTier.objects.filter(product_id=product.pk).delete()
    if product.publish_to_sellers:
        product.publish_to_sellers = False
        product.save(update_fields=['publish_to_sellers'])


def _legacy_delete_blocked(product, apps):
    OrderItem = apps.get_model('orders', 'OrderItem')
    ProductConsignmentRequest = apps.get_model('catalog', 'ProductConsignmentRequest')
    if OrderItem.objects.filter(product_id=product.pk).exists():
        return 'has_order_item'
    if ProductConsignmentRequest.objects.filter(product_id=product.pk).exists():
        return 'has_consignment_request'
    return ''


def hide_legacy_duplicate(product, ProductPriceTier, reason):
    ProductPriceTier.objects.filter(product_id=product.pk).delete()
    product.status = 'hidden'
    product.publish_to_sellers = False
    product.save(update_fields=['status', 'publish_to_sellers'])
    return reason


def remove_legacy_duplicate(product, canonical, apps, ProductPriceTier):
    if canonical is None or product.pk == canonical.pk:
        return 'skipped_missing_canonical'
    if article_key(product.article) in WHITELIST_ARTICLES:
        return 'skipped_whitelisted'
    blocked = _legacy_delete_blocked(product, apps)
    if blocked:
        return hide_legacy_duplicate(product, ProductPriceTier, f'deactivated_{blocked}')
    ProductPriceTier.objects.filter(product_id=product.pk).delete()
    product.delete()
    return 'deleted'


def list_suspected_ag_parts_duplicates(products):
    """Candidate duplicates only. Does not delete."""
    rows = list(products)
    by_normalized = {}
    by_compact = {}
    for product in rows:
        key = normalize_article(product.article)
        compact = compact_article(product.article)
        if key:
            by_normalized.setdefault(key, []).append(product)
        if compact:
            by_compact.setdefault(compact, []).append(product)

    suspected = []
    seen_ids = set()

    def add_group(group, reason):
        if len(group) < 2:
            return
        ids = tuple(sorted(item.pk for item in group))
        if ids in seen_ids:
            return
        seen_ids.add(ids)
        for item in group:
            suspected.append({
                'id': item.pk,
                'article': item.article,
                'title': item.title,
                'price': item.price,
                'seller_profile': getattr(item, 'seller_profile_id', None),
                'status': item.status,
                'reason_suspected_duplicate': reason,
            })

    for group in by_normalized.values():
        add_group(group, 'same_normalized_article')
    for group in by_compact.values():
        add_group(group, 'same_compact_article_hyphen_or_punct')
    return suspected


def clean_ag_parts_wholesale_assortment(apps, schema_editor):
    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    Product = apps.get_model('catalog', 'Product')
    ProductPriceTier = apps.get_model('catalog', 'ProductPriceTier')

    seller = SellerProfile.objects.filter(slug=AG_PARTS_SLUG).first()
    if seller is None:
        return

    products = list(ag_parts_products(Product, seller))
    by_article = {}
    for product in products:
        by_article.setdefault(article_key(product.article), []).append(product)

    for product in products:
        if article_key(product.article) in WHITELIST_ARTICLES:
            continue
        strip_wholesale(product, ProductPriceTier)

    for spec in LEGACY_DUPLICATES:
        canonicals = by_article.get(spec['canonical_article'], [])
        canonical = canonicals[0] if canonicals else None
        for leftover in by_article.get(spec['legacy_article'], []):
            remove_legacy_duplicate(leftover, canonical, apps, ProductPriceTier)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0025_ag_parts_description_avtozapchasti'),
        ('orders', '0006_wholesalefunnelevent'),
    ]

    operations = [
        migrations.RunPython(
            clean_ag_parts_wholesale_assortment,
            migrations.RunPython.noop,
        ),
    ]
