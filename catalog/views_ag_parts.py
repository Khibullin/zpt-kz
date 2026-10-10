"""AG Parts curated store page using existing products, without duplicate catalog rows."""
import re

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from catalog.applicability import parse_plain_list
from catalog.commercial import filter_products_by_vehicle
from catalog.models import Brand, CarModel, Product, SellerProfile
from catalog.views import attach_sellers_to_products
from catalog.wholesale import (
    WHOLESALE_TYPE_CHOICES,
    attach_public_wholesale_flags,
    public_wholesale_prefetch,
    wholesale_product_type,
)


def _product_fitment_models(product):
    models = []
    if product.car_model_id:
        models.append(product.car_model)
    models.extend(product.selected_models.all())
    return list({model.pk: model for model in models}.values())


def _model_is_named_in_clause(clause, model, sibling_names):
    labels = (f"{model.brand.name} {model.name}", model.name)
    for label in labels:
        match = re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", clause.casefold())
        if not match:
            continue
        remainder = clause[match.end():].casefold()
        for sibling_name in sibling_names:
            if sibling_name.casefold() == model.name.casefold():
                continue
            if sibling_name.casefold().startswith(model.name.casefold() + " "):
                suffix = sibling_name[len(model.name):].casefold()
                if remainder.startswith(suffix):
                    break
        else:
            return True
    return False


def _engine_code_is_in_clause(engine, clause):
    clause = clause.upper()
    engine = engine.upper()
    if re.search(rf"(?<![A-Z0-9]){re.escape(engine)}(?![A-Z0-9])", clause):
        return True

    # Expand short suffixes such as SQRE4T15B/C using the stored exact code.
    for shorthand in re.findall(r"\b[A-Z0-9]+/[A-Z]\b", clause):
        base, suffix = shorthand.split("/")
        if base[-1:].isalpha() and engine == base[:-1] + suffix:
            return True
    return False


def _model_engine_codes(product, model, sibling_names):
    """Return only codes paired with this model in compatibility text."""
    codes = parse_plain_list(product.engine_compatibility)
    if not codes:
        return set()

    compatibility = str(getattr(product, "compatibility", "") or "")
    clauses = re.split(r"[;\n]+", compatibility)
    model_clauses = [
        clause for clause in clauses
        if _model_is_named_in_clause(clause, model, sibling_names)
    ]
    if not model_clauses:
        # A product linked to one model has no cross-model ambiguity.
        if len(_product_fitment_models(product)) == 1:
            return {code for code in codes if not _explicitly_excludes_model_engine(product, model, code)}
        return set()

    compatible = set()
    excluded = set()
    for clause in model_clauses:
        is_exclusion = bool(re.search(r"\b(?:не|not|except|excluding)\b", clause.casefold()))
        for code in codes:
            if _engine_code_is_in_clause(code, clause):
                (excluded if is_exclusion else compatible).add(code)
    return compatible - excluded


def _explicitly_excludes_model_engine(product, model, engine):
    """Honor an explicit negative fitment statement for a model and engine."""
    compatibility = str(getattr(product, "compatibility", "") or "")
    for clause in re.split(r"[;\n]+", compatibility):
        if not re.search(r"\b(?:не|not|except|excluding)\b", clause.casefold()):
            continue
        if not _model_is_named_in_clause(clause, model, (model.name,)):
            continue
        if _engine_code_is_in_clause(engine, clause):
            return True
    return False


@require_GET
def ag_parts_store(request):
    seller = get_object_or_404(SellerProfile, slug="ag-parts")
    products = Product.objects.filter(
        seller_profile=seller,
        status="active",
    ).select_related(
        "brand", "car_model", "category", "seller_profile",
    ).prefetch_related("kaspi_listings").order_by("title", "id")
    products = list(public_wholesale_prefetch(products))
    attach_sellers_to_products(products)
    attach_public_wholesale_flags(products)
    return render(request, "catalog/ag_parts_store.html", {
        "seller": seller,
        "products": products,
        "page_title": "Наши товары AG Parts — автозапчасти | ZPT.KZ",
        "page_description": (
            "Ассортимент магазина AG Parts на ZPT.KZ. "
            "Подбор фильтров и запчастей, покупка через Kaspi при наличии прямой ссылки."
        ),
    })


@require_GET
def ag_parts_filter_finder(request):
    """Progressively filter AG Parts' existing products by make, model, and engine."""
    seller = get_object_or_404(SellerProfile, slug="ag-parts")
    base_products = list(
        Product.objects.filter(
            seller_profile=seller,
            status="active",
        ).select_related(
            "brand", "car_model", "car_model__brand", "category", "seller_profile",
        ).prefetch_related("selected_models", "selected_brands", "kaspi_listings")
    )

    model_ids = set()
    brand_ids = set()
    for product in base_products:
        product_models = _product_fitment_models(product)
        if product.brand_id:
            brand_ids.add(product.brand_id)
        brand_ids.update(brand.pk for brand in product.selected_brands.all())
        for model in product_models:
            model_ids.add(model.pk)
            brand_ids.add(model.brand_id)

    model_options = list(
        CarModel.objects.filter(pk__in=model_ids)
        .select_related("brand")
        .order_by("brand__name", "name")
        .values("id", "name", "brand_id", "brand__name")
    )
    brands = list(Brand.objects.filter(pk__in=brand_ids).order_by("name"))
    model_names_by_brand = {}
    for item in model_options:
        model_names_by_brand.setdefault(item["brand_id"], []).append(item["name"])

    engine_options_by_model = {}
    engine_missing_counts = {}
    for product in base_products:
        for model in _product_fitment_models(product):
            model_codes = _model_engine_codes(
                product,
                model,
                model_names_by_brand.get(model.brand_id, ()),
            )
            if model_codes:
                engine_options_by_model.setdefault(model.pk, set()).update(model_codes)
            else:
                engine_missing_counts[model.pk] = engine_missing_counts.get(model.pk, 0) + 1

    engine_options_by_model = {
        str(model_id): sorted(engines, key=str.casefold)
        for model_id, engines in engine_options_by_model.items()
    }

    brand_id = request.GET.get("brand", "").strip()
    model_id = request.GET.get("model", "").strip()
    selected_engine = request.GET.get("engine", "").strip()
    allowed_brand_ids = {str(brand.pk) for brand in brands}
    if brand_id not in allowed_brand_ids:
        brand_id = ""
        model_id = ""
        selected_engine = ""

    allowed_models = {
        str(item["id"]): item
        for item in model_options
        if str(item["brand_id"]) == brand_id
    }
    if model_id not in allowed_models:
        model_id = ""
        selected_engine = ""

    model_engine_options = engine_options_by_model.get(model_id, [])
    if selected_engine not in model_engine_options:
        selected_engine = ""

    has_selection = bool(brand_id)
    engine_not_recorded = bool(model_id and not model_engine_options)
    engine_missing_count = engine_missing_counts.get(int(model_id), 0) if model_id else 0

    products = []
    product_groups = []
    if has_selection:
        matching = Product.objects.filter(
            seller_profile=seller,
            status="active",
        ).select_related(
            "brand", "car_model", "car_model__brand", "category", "seller_profile",
        ).prefetch_related("selected_brands", "kaspi_listings")
        matching = filter_products_by_vehicle(matching, brand_id=brand_id)
        if model_id:
            matching = filter_products_by_vehicle(matching, model_id=model_id)

        result_queryset = public_wholesale_prefetch(matching)
        if selected_engine and model_id:
            model = CarModel.objects.select_related("brand").get(pk=model_id)
            candidates = list(
                result_queryset.filter(
                    engine_compatibility__icontains=selected_engine,
                ).order_by("title", "id")
            )
            sibling_names = model_names_by_brand.get(model.brand_id, ())
            products = [
                product for product in candidates
                if selected_engine in _model_engine_codes(product, model, sibling_names)
            ]
        else:
            products = list(result_queryset.order_by("title", "id"))

        attach_sellers_to_products(products)
        attach_public_wholesale_flags(products)

        grouped = {}
        for product in products:
            type_key = wholesale_product_type(product) or "other"
            grouped.setdefault(type_key, []).append(product)

        type_labels = {key: label for key, label in WHOLESALE_TYPE_CHOICES if key}
        type_order = [key for key, _label in WHOLESALE_TYPE_CHOICES]
        for type_key in type_order + ["other"]:
            if type_key not in grouped:
                continue
            product_groups.append({
                "label": type_labels.get(type_key, "Другие товары"),
                "products": grouped[type_key],
            })

    return render(request, "catalog/ag_parts_filter_finder.html", {
        "seller": seller,
        "brands": brands,
        "model_options": model_options,
        "engine_options_by_model": engine_options_by_model,
        "model_engine_options": model_engine_options,
        "selected_brand": brand_id,
        "selected_model": model_id,
        "selected_engine": selected_engine,
        "engine_not_recorded": engine_not_recorded,
        "engine_missing_count": engine_missing_count,
        "products": products,
        "product_groups": product_groups,
        "has_selection": has_selection,
        "page_title": "Подбор фильтров AG Parts по автомобилю | ZPT.KZ",
        "page_description": (
            "Выберите марку, модель и двигатель, чтобы посмотреть фильтры и свечи "
            "AG Parts с сохранённой применяемостью."
        ),
    })
