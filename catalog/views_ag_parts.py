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


def _explicitly_excludes_model_engine(product, model, engine):
    """Honor a model+engine exclusion written explicitly in compatibility text."""
    compatibility = str(getattr(product, "compatibility", "") or "")
    model_labels = (
        f"{model.brand.name} {model.name}",
        model.name,
    )
    for clause in re.split(r"[;\n.!]+", compatibility):
        lowered = clause.casefold()
        if not re.search(r"\b(?:не|not|except|excluding)\b", lowered):
            continue
        if engine.casefold() not in lowered:
            continue
        if any(
            re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", lowered)
            for label in model_labels if label
        ):
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
        ).prefetch_related(
            "selected_models", "selected_brands", "kaspi_listings",
        )
    )

    # Build make/model and engine options only from currently active AG Parts items.
    model_ids = set()
    brand_ids = set()
    engine_options_by_model = {}
    engine_missing_counts = {}
    for product in base_products:
        product_models = []
        if product.car_model_id:
            product_models.append(product.car_model)
        product_models.extend(product.selected_models.all())

        product_brands = set()
        if product.brand_id:
            product_brands.add(product.brand_id)
        product_brands.update(brand.pk for brand in product.selected_brands.all())

        engines = parse_plain_list(product.engine_compatibility)
        for model in product_models:
            model_ids.add(model.pk)
            product_brands.add(model.brand_id)
            if not engines:
                engine_missing_counts[model.pk] = engine_missing_counts.get(model.pk, 0) + 1
            for engine in engines:
                if not _explicitly_excludes_model_engine(product, model, engine):
                    engine_options_by_model.setdefault(model.pk, set()).add(engine)
        brand_ids.update(product_brands)

    model_options = list(
        CarModel.objects.filter(pk__in=model_ids)
        .select_related("brand")
        .order_by("brand__name", "name")
        .values("id", "name", "brand_id", "brand__name")
    )
    brands = list(Brand.objects.filter(pk__in=brand_ids).order_by("name"))
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
        ).prefetch_related("selected_models", "selected_brands", "kaspi_listings")
        matching = filter_products_by_vehicle(matching, brand_id=brand_id)
        if model_id:
            matching = filter_products_by_vehicle(matching, model_id=model_id)

        if selected_engine and model_id:
            model = CarModel.objects.select_related("brand").get(pk=model_id)
            candidates = list(matching.filter(engine_compatibility__icontains=selected_engine))
            matching_ids = [
                product.pk for product in candidates
                if selected_engine.casefold() in {
                    value.casefold()
                    for value in parse_plain_list(product.engine_compatibility)
                }
                and not _explicitly_excludes_model_engine(product, model, selected_engine)
            ]
            matching = matching.filter(pk__in=matching_ids)

        products = list(
            public_wholesale_prefetch(matching).order_by("title", "id")
        )
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
