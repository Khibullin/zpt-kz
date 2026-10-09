"""Deterministic candidate discovery. Never publishes or infers compatibility."""
from django.utils.text import slugify

from catalog.models import Product
from core.models import EditorialPage, EditorialCandidate


def collect_product_candidates(*, limit=10, create=False):
    """Find eligible catalog topics; create candidates only, never public pages."""
    found = []
    products = (Product.objects.filter(status='active')
        .exclude(article='').exclude(title='').exclude(slug='')
        .order_by('pk').only('pk', 'article', 'title', 'slug', 'description', 'brand_id'))
    for product in products.iterator():
        if len(found) >= limit:
            break
        if not product.brand_id:
            continue
        if not (product.description or '').strip():
            continue
        key = f'product:{product.pk}'
        if EditorialCandidate.objects.filter(source_key=key).exists():
            continue
        title = f'Как проверить подбор детали: {product.title[:140]}'
        proposal = {
            'source_key': key,
            'title': title[:240],
            'source_product_id': product.pk,
            'rationale': 'Активный товар с артикулом и описанием; совместимость требует ручной проверки.',
        }
        found.append(proposal)
        if create:
            EditorialCandidate.objects.get_or_create(
                source_key=key,
                defaults={
                    'title': proposal['title'],
                    'source_product_id': product.pk,
                    'rationale': proposal['rationale'],
                },
            )
    return found
