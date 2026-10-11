"""AG Parts curated store page using existing products, without duplicate catalog rows."""
import re
from datetime import date

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
    lowered_clause = clause.casefold()
    for label in labels:
        match = re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", lowered_clause)
        if not match:
            continue
        remainder = lowered_clause[match.end():]
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


def _compatibility_clauses(compatibility):
    """Split model fitment from following negative notes without splitting decimals."""
    return [
        clause.strip()
        for clause in re.split(
            r"[;\n]+|(?<=[.!?])\s+(?=(?:не|not|except|excluding)\b)",
            str(compatibility or ""),
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]


def _explicitly_excludes_model_engine(product, model, engine, sibling_names):
    compatibility = str(getattr(product, "compatibility", "") or "")
    for clause in _compatibility_clauses(compatibility):
        if not re.search(r"\b(?:не|not|except|excluding)\b", clause.casefold()):
            continue
        if not _model_is_named_in_clause(clause, model, sibling_names):
            continue
        if _engine_code_is_in_clause(engine, clause):
            return True
    return False


def _model_engine_codes(product, model, sibling_names):
    """Return only codes paired with this model in compatibility text."""
    codes = parse_plain_list(product.engine_compatibility)
    if not codes:
        return set()

    compatibility = str(getattr(product, "compatibility", "") or "")
    clauses = _compatibility_clauses(compatibility)
    model_clauses = [
        clause for clause in clauses
        if _model_is_named_in_clause(clause, model, sibling_names)
    ]
    if not model_clauses:
        # A single-model product has no cross-model ambiguity.
        if len(_product_fitment_models(product)) == 1:
            return {
                code for code in codes
                if not _explicitly_excludes_model_engine(
                    product, model, code, sibling_names,
                )
            }
        return set()

    compatible = set()
    excluded = set()
    for clause in model_clauses:
        is_exclusion = bool(re.search(r"\b(?:не|not|except|excluding)\b", clause.casefold()))
        for code in codes:
            if _engine_code_is_in_clause(code, clause):
                (excluded if is_exclusion else compatible).add(code)
    return compatible - excluded


def _model_name_segments(clause, model, sibling_names):
    """Return text attached to this exact model mention, stopping at the next model."""
    names = sorted(set(sibling_names), key=lambda name: (-len(name), name.casefold()))
    mentions = []
    for name in names:
        pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
        for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
            mentions.append((match.start(), match.end(), name))

    accepted = []
    for start, end, name in sorted(mentions, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < prior_end and end > prior_start for prior_start, prior_end, _ in accepted):
            continue
        accepted.append((start, end, name))
    accepted.sort(key=lambda item: item[0])

    segments = []
    for index, (start, end, name) in enumerate(accepted):
        if name.casefold() != model.name.casefold():
            continue
        prefix_tail = clause[max(0, start - 60):start]
        if re.search(
            r"\b(?:не|not|except|excluding)(?:\s+\S+){0,3}\s*$",
            prefix_tail,
            flags=re.IGNORECASE,
        ):
            continue
        next_start = accepted[index + 1][0] if index + 1 < len(accepted) else len(clause)
        segments.append(clause[end:next_start])

    target_mention_found = any(
        name.casefold() == model.name.casefold()
        for _start, _end, name in accepted
    )
    if (
        not segments
        and not target_mention_found
        and _model_is_named_in_clause(clause, model, sibling_names)
    ):
        return [clause]
    return segments


_YEAR_FRAGMENT = r"(?:(?:0?[1-9]|1[0-2])\.)?(?:19|20)\d{2}"
_YEAR_RANGE_RE = re.compile(
    rf"(?<!\d)(?P<start>{_YEAR_FRAGMENT})\s*[–—-]\s*(?P<end>{_YEAR_FRAGMENT})(?!\d)",
    re.IGNORECASE,
)
_YEAR_SINCE_RE = re.compile(
    rf"\bс\s+(?P<year>{_YEAR_FRAGMENT})\b",
    re.IGNORECASE,
)


def _year_in_fragment(fragment):
    match = re.search(r"(?:19|20)\d{2}", fragment)
    return int(match.group(0)) if match else None


def _year_ranges_in_text(text):
    """Extract only explicit ranges and ‘since year’ notes from fitment text."""
    ranges = []
    occupied = []
    for match in _YEAR_RANGE_RE.finditer(text):
        first = _year_in_fragment(match.group("start"))
        last = _year_in_fragment(match.group("end"))
        if first and last and first <= last:
            ranges.append((match.start(), match.end(), first, last))
            occupied.append(match.span())

    current_year = date.today().year
    for match in _YEAR_SINCE_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        first = _year_in_fragment(match.group("year"))
        if first:
            ranges.append((match.start(), match.end(), first, max(first, current_year)))

    return ranges


def _engine_mentions_in_text(text, codes):
    mentions = {}
    upper_text = text.upper()
    for code in codes:
        code_upper = code.upper()
        spans = [
            match.span()
            for match in re.finditer(
                rf"(?<![A-Z0-9]){re.escape(code_upper)}(?![A-Z0-9])",
                upper_text,
            )
        ]
        if not spans:
            for match in re.finditer(r"\b[A-Z0-9]+/[A-Z]\b", upper_text):
                base, suffix = match.group(0).split("/")
                if base[-1:].isalpha() and code_upper == base[:-1] + suffix:
                    spans.append(match.span())
        if spans:
            mentions[code] = spans
    return mentions


def _product_model_years(product, model, sibling_names):
    """Return year coverage for a model, plus coverage tied to exact engine codes."""
    codes = parse_plain_list(product.engine_compatibility)
    compatible_codes = _model_engine_codes(product, model, sibling_names)
    engine_years = {code: set() for code in compatible_codes}
    model_years = set()

    for clause in _compatibility_clauses(getattr(product, "compatibility", "")):
        for segment in _model_name_segments(clause, model, sibling_names):
            ranges = _year_ranges_in_text(segment)
            if not ranges:
                continue

            expanded = set()
            for _start, _end, year_from, year_to in ranges:
                expanded.update(range(year_from, year_to + 1))
            model_years.update(expanded)

            mentions = _engine_mentions_in_text(segment, compatible_codes)
            if not mentions and len(_product_fitment_models(product)) == 1:
                # A single-model listing can keep engine codes in a separate field.
                mentions = {code: [] for code in compatible_codes}
            if not mentions:
                continue

            unique_ranges = {(item[2], item[3]) for item in ranges}
            if len(unique_ranges) == 1:
                year_from, year_to = next(iter(unique_ranges))
                covered = set(range(year_from, year_to + 1))
                for code in mentions:
                    engine_years.setdefault(code, set()).update(covered)
                continue

            positioned_mentions = [
                (code, span)
                for code, spans in mentions.items()
                for span in spans
            ]
            for year_range in ranges:
                preceding = [
                    (code, span)
                    for code, span in positioned_mentions
                    if span[1] <= year_range[0]
                ]
                if preceding:
                    target_codes = {
                        max(preceding, key=lambda item: item[1][1])[0]
                    }
                else:
                    following = [
                        (code, span)
                        for code, span in positioned_mentions
                        if span[0] >= year_range[1]
                    ]
                    target_codes = (
                        {min(following, key=lambda item: item[1][0])[0]}
                        if following else set()
                    )
                for code in target_codes:
                    engine_years.setdefault(code, set()).update(
                        range(year_range[2], year_range[3] + 1)
                    )

    return {
        "years": model_years,
        "engine_years": engine_years,
    }


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
    for item in model_options:
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

    model_year_options = year_options_by_model.get(model_id, [])
    if selected_year not in {str(year) for year in model_year_options}:
        selected_year = ""

    all_model_engine_options = engine_options_by_model.get(model_id, [])
    model_engine_options = all_model_engine_options
    if selected_year:
        model_engine_options = engine_options_by_model_and_year.get(model_id, {}).get(selected_year, [])
    if selected_engine not in model_engine_options:
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
        selected_model = (
            CarModel.objects.select_related("brand").get(pk=model_id)
            if model_id else None
        )
        sibling_names = (
            model_names_by_brand.get(selected_model.brand_id, ())
            if selected_model else ()
        )
        if selected_engine and model_id:
            candidates = list(
                result_queryset.filter(
                    engine_compatibility__icontains=selected_engine,
                ).order_by("title", "id")
            )
            products = [
                product for product in candidates
                if selected_engine in _model_engine_codes(
                    base_product_by_id.get(product.pk, product),
                    selected_model,
                    sibling_names,
                )
            ]
        else:
            products = list(result_queryset.order_by("title", "id"))

        if selected_year and selected_model:
            year = int(selected_year)
            year_filtered = []
            for product in products:
                fitment_product = base_product_by_id.get(product.pk, product)
                year_data = fitment_year_data.get((product.pk, selected_model.pk))
                if year_data is None:
                    year_data = _product_model_years(fitment_product, selected_model, sibling_names)

                if selected_engine:
                    covered_years = year_data["engine_years"].get(selected_engine, set())
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

    return render(request, "catalog/ag_parts_filter_finder.html", {
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
