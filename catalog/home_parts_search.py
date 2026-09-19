"""Public catalog search for the homepage short form."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Value
from django.db.models.functions import Replace, Upper

from catalog.article_utils import normalize_article
from catalog.commercial import filter_products_by_vehicle
from catalog.models import Brand as CatalogBrand
from catalog.models import CarModel as CatalogCarModel
from catalog.models import Product
from catalog.templatetags.product_extras import public_product_url
from catalog.wholesale import attach_public_wholesale_flags, public_wholesale_prefetch
from core.services.home_parts_query import looks_like_exact_article

MAX_RESULTS_PER_GROUP = 8
COMPAT_UNKNOWN = 'unknown'
COMPAT_UNCONFIRMED = 'unconfirmed'
_ARTICLE_STRIP_CHARS = ('-', ' ', '/', '.', '_')


@dataclass
class ProductHit:
    product: Product
    match_kind: str
    compatibility: str


@dataclass
class SearchGroup:
    query: str
    products: list[Product] = field(default_factory=list)
    hits: list[ProductHit] = field(default_factory=list)


def _catalog_vehicle_ids(
    brand_name: str,
    model_name: str,
    *,
    country: str = '',
    transport_type: str = '',
) -> tuple[list[int], list[int], bool, bool]:
    del transport_type
    brand_name = ' '.join(str(brand_name or '').split())
    model_name = ' '.join(str(model_name or '').split())
    country = ' '.join(str(country or '').split())
    brand_ids: list[int] = []
    model_ids: list[int] = []
    ambiguous = False
    country_mismatch = False

    if brand_name:
        brands = list(
            CatalogBrand.objects.select_related('country').filter(name__iexact=brand_name)
        )
        if country:
            narrowed = [
                item for item in brands
                if item.country_id and item.country.name.casefold() == country.casefold()
            ]
            if brands and not narrowed:
                country_mismatch = True
                brands = []
            else:
                brands = narrowed
        brand_ids = [item.id for item in brands]
        if len(brands) > 1:
            ambiguous = True

    if model_name and brand_ids:
        models = list(
            CatalogCarModel.objects.filter(
                brand_id__in=brand_ids,
                name__iexact=model_name,
            )
        )
        model_ids = [item.id for item in models]
        if len(models) > 1:
            ambiguous = True
    elif model_name and not brand_ids:
        # Do not pick a same-named model from another brand.
        model_ids = []
    return brand_ids, model_ids, ambiguous, country_mismatch


def _base_products():
    products = Product.objects.filter(status='active').select_related(
        'brand',
        'brand__country',
        'car_model',
        'car_model__brand',
        'category',
        'seller_profile',
    )
    return public_wholesale_prefetch(products)


def _filter_by_catalog_vehicle(qs, brand_ids: list[int], model_ids: list[int]):
    if not brand_ids and not model_ids:
        return qs
    combined = qs.none()
    if model_ids and brand_ids:
        pairs = []
        models = list(CatalogCarModel.objects.filter(id__in=model_ids).only('id', 'brand_id'))
        for item in models:
            pairs.append((item.brand_id, item.id))
        if not pairs:
            for brand_id in brand_ids:
                for model_id in model_ids:
                    pairs.append((brand_id, model_id))
        for brand_id, model_id in pairs:
            combined = combined | filter_products_by_vehicle(
                qs,
                brand_id=str(brand_id),
                model_id=str(model_id),
            )
    elif model_ids:
        for model_id in model_ids:
            combined = combined | filter_products_by_vehicle(qs, model_id=str(model_id))
    else:
        for brand_id in brand_ids:
            combined = combined | filter_products_by_vehicle(qs, brand_id=str(brand_id))
    return combined.distinct()


def _compact_article_expr():
    expr = Upper('article')
    for char in _ARTICLE_STRIP_CHARS:
        expr = Replace(expr, Value(char), Value(''))
    return expr


def _exact_article_candidates(qs, query: str):
    compact = normalize_article(query)
    if not compact:
        return []
    annotated = qs.exclude(article='').annotate(article_compact=_compact_article_expr())
    return list(
        annotated.filter(article_compact=compact)[:MAX_RESULTS_PER_GROUP]
    )


def _search_position(
    query: str,
    *,
    brand_name: str,
    model_name: str,
    country: str = '',
    transport_type: str = '',
) -> SearchGroup:
    group = SearchGroup(query=query)
    qs = _base_products()
    brand_ids, model_ids, vehicle_ambiguous, country_mismatch = _catalog_vehicle_ids(
        brand_name,
        model_name,
        country=country,
        transport_type=transport_type,
    )
    exact_article = looks_like_exact_article(query)
    user_brand = bool(brand_name)
    user_model = bool(model_name)
    user_vehicle = user_brand or user_model
    model_missing = user_model and not model_ids
    brand_missing = user_brand and not brand_ids
    vehicle_for_name_search = (
        not country_mismatch
        and not brand_missing
        and not model_missing
        and (
            (user_model and bool(model_ids))
            or (user_brand and not user_model and bool(brand_ids))
            or not user_vehicle
        )
    )
    vehicle_filter_ids = brand_ids
    vehicle_filter_model_ids = model_ids if user_model else []
    vehicle_filtered_ids: set[int] = set()
    if brand_ids or model_ids:
        vehicle_filtered_ids = set(
            _filter_by_catalog_vehicle(
                qs,
                vehicle_filter_ids,
                vehicle_filter_model_ids,
            ).values_list('id', flat=True)
        )

    def compatibility_for(product: Product, kind: str) -> str:
        if (
            vehicle_ambiguous
            or country_mismatch
            or brand_missing
            or model_missing
            or (user_vehicle and product.id not in vehicle_filtered_ids)
        ):
            return COMPAT_UNCONFIRMED
        if kind in {'article_exact', 'article'} and user_vehicle:
            if product.id not in vehicle_filtered_ids:
                return COMPAT_UNCONFIRMED
        return COMPAT_UNKNOWN

    found_ids: set[int] = set()
    ranked: list[ProductHit] = []

    for product in _exact_article_candidates(qs, query):
        if product.id in found_ids:
            continue
        found_ids.add(product.id)
        ranked.append(ProductHit(
            product=product,
            match_kind='article_exact',
            compatibility=compatibility_for(product, 'article_exact'),
        ))

    if len(ranked) < MAX_RESULTS_PER_GROUP and vehicle_for_name_search:
        article_hits = qs.filter(article__icontains=query).exclude(pk__in=found_ids)
        if user_vehicle and (brand_ids or model_ids):
            article_hits = _filter_by_catalog_vehicle(
                article_hits,
                brand_ids,
                model_ids if user_model else [],
            )
        for product in article_hits[:MAX_RESULTS_PER_GROUP]:
            if product.id in found_ids:
                continue
            found_ids.add(product.id)
            ranked.append(ProductHit(
                product=product,
                match_kind='article',
                compatibility=compatibility_for(product, 'article'),
            ))
            if len(ranked) >= MAX_RESULTS_PER_GROUP:
                break

    if len(ranked) < MAX_RESULTS_PER_GROUP and not exact_article and vehicle_for_name_search:
        title_hits = qs.filter(title__icontains=query).exclude(pk__in=found_ids)
        if user_vehicle and (brand_ids or model_ids):
            title_hits = _filter_by_catalog_vehicle(
                title_hits,
                brand_ids,
                model_ids if user_model else [],
            )
        for product in title_hits[:MAX_RESULTS_PER_GROUP]:
            if product.id in found_ids:
                continue
            found_ids.add(product.id)
            ranked.append(ProductHit(
                product=product,
                match_kind='title',
                compatibility=(
                    COMPAT_UNCONFIRMED if vehicle_ambiguous else COMPAT_UNKNOWN
                ),
            ))
            if len(ranked) >= MAX_RESULTS_PER_GROUP:
                break

    ranked = ranked[:MAX_RESULTS_PER_GROUP]
    products = [hit.product for hit in ranked]
    from catalog.views import attach_sellers_to_products
    attach_sellers_to_products(products)
    attach_public_wholesale_flags(products)
    group.hits = ranked
    group.products = products
    return group


def search_home_parts(
    queries: list[str],
    *,
    brand_name: str = '',
    model_name: str = '',
    country: str = '',
    transport_type: str = '',
) -> list[SearchGroup]:
    groups = []
    for query in queries:
        text = ' '.join(str(query or '').split())
        if not text:
            continue
        groups.append(
            _search_position(
                text,
                brand_name=brand_name,
                model_name=model_name,
                country=country,
                transport_type=transport_type,
            )
        )
    return groups


def groups_to_json(groups: list[SearchGroup]) -> list[dict]:
    payload = []
    for group in groups:
        items = []
        for hit in group.hits:
            product = hit.product
            image_url = ''
            if getattr(product, 'main_image', None):
                try:
                    image_url = product.main_image.url
                except ValueError:
                    image_url = ''
            items.append({
                'id': product.id,
                'title': product.title,
                'article': product.article,
                'url': public_product_url(product),
                'price': product.price,
                'price_on_request': bool(product.price_on_request),
                'image_url': image_url,
                'match_kind': hit.match_kind,
                'compatibility': hit.compatibility,
            })
        payload.append({
            'query': group.query,
            'products': items,
        })
    return payload
