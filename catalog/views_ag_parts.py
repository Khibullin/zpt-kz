"""AG Parts curated store page using existing products, without duplicate catalog rows."""
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from catalog.vehicle_fitment import (
    _product_fitment_models,
    _model_engine_codes,
    _product_model_years,
    product_selection_notices,
)
from catalog.models import Brand, CarModel, Product, SellerProfile
from catalog.views import attach_sellers_to_products
from catalog.wholesale import (
    WHOLESALE_TYPE_CHOICES,
    attach_public_wholesale_flags,
    public_wholesale_prefetch,
    wholesale_product_type,
)


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
        ).prefetch_related("selected_models__brand", "selected_brands", "kaspi_listings", "fitments__car_model__brand")
    )

    model_ids = set()
    brand_ids = set()
    for product in base_products:
        if product.brand_id:
            brand_ids.add(product.brand_id)
        brand_ids.update(brand.pk for brand in product.selected_brands.all())
        for model in _product_fitment_models(product):
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
    for item in CarModel.objects.filter(brand_id__in=brand_ids).values("brand_id", "name"):
        model_names_by_brand.setdefault(item["brand_id"], []).append(item["name"])

    engine_options_by_model = {}
    engine_year_options_by_model = {}
    year_options_by_model = {}
    engine_missing_counts = {}
    base_product_by_id = {product.pk: product for product in base_products}
    fitment_year_data = {}

    for product in base_products:
        for model in _product_fitment_models(product):
            sibling_names = model_names_by_brand.get(model.brand_id, ())
            model_codes = _model_engine_codes(product, model, sibling_names)
            if model_codes:
                engine_options_by_model.setdefault(model.pk, set()).update(model_codes)
            else:
                engine_missing_counts[model.pk] = engine_missing_counts.get(model.pk, 0) + 1

            year_data = _product_model_years(product, model, sibling_names)
            fitment_year_data[(product.pk, model.pk)] = year_data
            year_options_by_model.setdefault(model.pk, set()).update(year_data["years"])
            per_engine = engine_year_options_by_model.setdefault(model.pk, {})
            for code, years in year_data["engine_years"].items():
                per_engine.setdefault(code, set()).update(years)

    year_options_by_model = {
        str(model_id): sorted(years, reverse=True)
        for model_id, years in year_options_by_model.items()
    }
    engine_year_options_by_model = {
        str(model_id): {
            code: sorted(years, reverse=True)
            for code, years in engines.items()
        }
        for model_id, engines in engine_year_options_by_model.items()
    }
    engine_options_by_model = {
        str(model_id): sorted(engines, key=str.casefold)
        for model_id, engines in engine_options_by_model.items()
    }

    engine_options_by_model_and_year = {}
    for model_id, engines in engine_options_by_model.items():
        years_for_model = year_options_by_model.get(model_id, [])
        engine_years = engine_year_options_by_model.get(model_id, {})
        engine_options_by_model_and_year[model_id] = {
            str(year): [
                engine for engine in engines
                if year in engine_years.get(engine, [])
            ]
            for year in years_for_model
        }

    brand_id = request.GET.get("brand", "").strip()
    model_id = request.GET.get("model", "").strip()
    selected_year = request.GET.get("year", "").strip()
    selected_engine = request.GET.get("engine", "").strip()
    article_query = request.GET.get("article", "").strip()
    article_searched = bool(article_query)
    allowed_brand_ids = {str(brand.pk) for brand in brands}
    if brand_id not in allowed_brand_ids:
        brand_id = ""
        model_id = ""
        selected_engine = ""
        selected_year = ""

    allowed_models = {
        str(item["id"]): item
        for item in model_options
        if str(item["brand_id"]) == brand_id
    }
    if model_id not in allowed_models:
        model_id = ""
        selected_year = ""
        selected_engine = ""

    invalid_selection = False
    model_year_options = year_options_by_model.get(model_id, [])
    if selected_year and selected_year not in {str(year) for year in model_year_options}:
        invalid_selection = True
        selected_year = ""

    all_model_engine_options = engine_options_by_model.get(model_id, [])
    model_engine_options = all_model_engine_options
    if selected_year:
        model_engine_options = engine_options_by_model_and_year.get(model_id, {}).get(selected_year, [])
    if selected_engine and selected_engine not in model_engine_options:
        invalid_selection = True
        selected_engine = ""

    has_selection = bool(brand_id or article_searched)
    year_not_recorded = bool(model_id and not model_year_options)
    engine_not_recorded = bool(model_id and not all_model_engine_options)
    if not model_id:
        engine_placeholder = "Сначала выберите модель"
    elif selected_year and all_model_engine_options and not model_engine_options:
        engine_placeholder = "Двигатели для этого года не подтверждены"
    elif engine_not_recorded:
        engine_placeholder = "Коды двигателей не указаны"
    elif selected_year:
        engine_placeholder = "Все двигатели для выбранного года"
    else:
        engine_placeholder = "Все двигатели модели"

    if not model_id:
        year_placeholder = "Сначала выберите модель"
    elif year_not_recorded:
        year_placeholder = "Годы в каталоге не указаны"
    else:
        year_placeholder = "Все годы модели"

    engine_missing_count = engine_missing_counts.get(int(model_id), 0) if model_id else 0
    year_missing_count = 0
    products = []
    product_groups = []
    if article_searched:
        normalized_article = article_query.casefold()
        products = [
            product for product in base_products
            if str(product.article or "").strip().casefold() == normalized_article
        ]
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
    elif has_selection:
        matching_ids = [product.pk for product in base_products if (
            (not brand_id or int(brand_id) in (
                {product.brand_id}
                | {brand.pk for brand in product.selected_brands.all()}
                | {model.brand_id for model in _product_fitment_models(product)}
            )) and (not model_id or int(model_id) in {model.pk for model in _product_fitment_models(product)})
        )]
        matching = Product.objects.filter(
            seller_profile=seller,
            status="active",
            pk__in=matching_ids,
        ).select_related(
            "brand", "car_model", "car_model__brand", "category", "seller_profile",
        ).prefetch_related("selected_brands", "kaspi_listings")
        result_queryset = public_wholesale_prefetch(matching)
        selected_model = (
            CarModel.objects.select_related("brand").get(pk=model_id)
            if model_id else None
        )
        sibling_names = (
            model_names_by_brand.get(selected_model.brand_id, ())
            if selected_model else ()
        )
        products = list(result_queryset.order_by("title", "id"))
        if invalid_selection:
            products = []
        elif selected_engine and selected_model:
            products = [product for product in products if (
                selected_engine in _model_engine_codes(base_product_by_id[product.pk], selected_model, sibling_names)
                or fitment_year_data[(product.pk, selected_model.pk)].get('engine_independent', False)
            )]

        if selected_year and selected_model:
            year = int(selected_year)
            year_filtered = []
            for product in products:
                fitment_product = base_product_by_id.get(product.pk, product)
                year_data = fitment_year_data.get((product.pk, selected_model.pk))
                if year_data is None:
                    year_data = _product_model_years(fitment_product, selected_model, sibling_names)

                if selected_engine:
                    covered_years = year_data["engine_years"].get(selected_engine, set()) | year_data.get("wildcard_years", set())
                else:
                    covered_years = year_data["years"]

                if not covered_years:
                    year_missing_count += 1
                elif year in covered_years:
                    year_filtered.append(product)
            products = year_filtered

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

    fitment_notices = []
    if model_id and not article_searched and selected_year:
        for product in products:
            for notice in product_selection_notices(base_product_by_id[product.pk], selected_model, selected_year, selected_engine):
                fitment_notices.append(f'{product.article}: {notice}')
    return render(request, "catalog/ag_parts_filter_finder.html", {
        "invalid_selection": invalid_selection,
        "fitment_notices": list(dict.fromkeys(fitment_notices)),
        "seller": seller,
        "brands": brands,
        "model_options": model_options,
        "engine_options_by_model": engine_options_by_model,
        "engine_options_by_model_and_year": engine_options_by_model_and_year,
        "model_year_options": model_year_options,
        "model_engine_options": model_engine_options,
        "engine_placeholder": engine_placeholder,
        "year_placeholder": year_placeholder,
        "selected_brand": brand_id,
        "selected_model": model_id,
        "selected_year": selected_year,
        "selected_engine": selected_engine,
        "article_query": article_query,
        "article_searched": article_searched,
        "year_not_recorded": year_not_recorded,
        "year_missing_count": year_missing_count if has_selection else 0,
        "engine_not_recorded": engine_not_recorded,
        "engine_missing_count": engine_missing_count,
        "products": products,
        "product_groups": product_groups,
        "has_selection": has_selection,
        "page_title": "Подбор фильтров AG Parts по автомобилю | ZPT.KZ",
        "page_description": (
            "Выберите марку, модель, год выпуска и двигатель, чтобы посмотреть фильтры "
            "и свечи AG Parts с сохранённой применяемостью."
        ),
    })
