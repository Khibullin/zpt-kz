"""Explainable specialization checks. Unknown imported coverage is never a match."""
import re
from dataclasses import dataclass


def fold(value):
    return ' '.join(str(value or '').split()).casefold()


def requested_models(req):
    # Split explicit alternatives only; preserve model names such as CX-5 and UNI-K.
    return [x.strip() for x in re.split(r'\s*[/;,]\s*|\s+и\s+', req.model or '') if x.strip()]


def category_names(req):
    categories = {fold(req.category)}
    # Spark plugs are stored under either parent in existing seller profiles.
    if re.search(r'\bсвеч[аиуыей]', fold(getattr(req, 'description', ''))) and categories & {'двигатель', 'электрика'}:
        categories.update(('двигатель', 'электрика', 'свечи зажигания', 'система зажигания'))
    return categories


@dataclass(frozen=True)
class Assessment:
    status: str
    reason: str
    models: tuple = ()
    rank: tuple = ()


def assess(seller, req):
    if getattr(seller, 'audit_status', 'unchecked') == 'conflict':
        return Assessment('review', 'Требует проверки: обнаружены расхождения города или специализации.')
    from core.services.seller_request_filters import matches_request
    brands = {fold(x.name) for x in seller.selected_brands.all()}
    models = {fold(x.name) for x in seller.selected_models.all()}
    categories = {fold(x.name) for x in seller.selected_categories.all()}
    brands.update(fold(x) for x in (seller.brand, seller.brand_fk.name if seller.brand_fk_id else '') if x)
    models.update(fold(x) for x in (seller.model, seller.model_fk.name if seller.model_fk_id else '') if x)
    categories.update(fold(x) for x in (seller.category,) if x)
    wanted = requested_models(req)
    filters = seller.request_filters
    explicit_brand = fold(req.brand) in brands
    explicit_category = bool(category_names(req) & categories)
    countries = set()
    if hasattr(seller, 'selected_countries'):
        countries.update(fold(c.name) for c in seller.selected_countries.all())
    if getattr(seller, 'country_fk_id', None):
        countries.add(fold(seller.country_fk.name))
    country = fold(getattr(req, 'country', ''))
    if not country and countries and filters is None:
        key = (fold(req.brand), req.transport_type)
        cached = getattr(req, '_matching_country', None)
        if cached is None or cached[0] != key:
            from core.models import Brand
            names = set(Brand.objects.filter(name__iexact=req.brand, transport_type=req.transport_type)
                        .values_list('country__name', flat=True))
            cached = (key, fold(next(iter(names))) if len(names) == 1 else '')
            req._matching_country = cached
        country = cached[1]
        if not country and not explicit_brand:
            return Assessment('review', 'Требует проверки: страна марки не определена.')

    if filters is None and countries and country and country not in countries:
        return Assessment('excluded', 'Страна марки не соответствует специализации.')
    country_match = bool(country and country in countries)

    if filters is not None:
        explicit_brand = any(fold(b['name']) == fold(req.brand) for b in filters.get('brands', []))
        explicit_category = any(fold(c['name']) in category_names(req) for c in filters.get('categories', []))
        if not matches_request(filters, req):
            return Assessment('excluded', 'Марка, модель или категория не соответствует фильтру продавца.')
        brand_filters = [b for b in filters.get('brands', []) if fold(b['name']) == fold(req.brand)]
        allowed_models = {fold(m['name']) for b in brand_filters for m in b.get('models', [])}
        covers_all = filters.get('all_brands') or any(b.get('all_models') for b in brand_filters)
        matched = [m for m in wanted if covers_all or fold(m) in allowed_models]
    else:
        if seller.transport_type != req.transport_type:
            return Assessment('excluded', 'Тип транспорта не совпадает.')
        if brands and not explicit_brand:
            return Assessment('excluded', 'Продавец работает с другими марками.')
        if not brands and not seller.all_brands and not country_match:
            return Assessment('review', 'Требует проверки: марка не указана.')
        if categories and not explicit_category:
            return Assessment('excluded', 'Продавец работает с другими категориями.')
        if not categories and not seller.all_categories:
            return Assessment('review', 'Требует проверки: категория не указана.')
        matched = [m for m in wanted if not models or seller.all_models or fold(m) in models]
        if models and not seller.all_models and not matched:
            return Assessment('excluded', 'Продавец работает с другими моделями.')
        if not models and not seller.all_models and not seller.all_brands and not country_match:
            return Assessment('review', 'Требует проверки: модели не указаны.')
    # A catalogue import's broad default flags are not evidence of specialization.
    # Explicit, saved seller filters are trusted; no source records are rewritten.
    registered = bool(getattr(seller, 'user_id', None) or getattr(seller, 'has_registration_password', getattr(seller, 'password_hash', ''))) and not seller.catalog_only
    if filters is None and not registered and (not (explicit_brand or country_match) or not explicit_category):
        return Assessment('review', 'Требует проверки: марка или категория карточки не подтверждена.')
    matched = tuple(m for m in matched if m)
    model_specific = bool(models) and not seller.all_models
    if filters is not None:
        model_specific = any(not b.get('all_models') for b in filters.get('brands', []) if fold(b['name']) == fold(req.brand))
    model_label = ', '.join(matched) if model_specific else 'все модели'
    category_label = ', '.join(sorted(category_names(req) & categories)) if explicit_category and categories else (req.category if explicit_category else 'все категории')
    reason = ' · '.join(x for x in (seller.city, country if country_match else '', req.brand if explicit_brand else 'все марки', model_label, category_label) if x)
    rank = (not country_match, not explicit_brand, not model_specific, not explicit_category, not registered)
    return Assessment('matched', reason, matched, rank)
