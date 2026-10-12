"""Cascading kit selection using the same product fitment as the filter finder.

The kit's own year/engine fields can narrow product coverage, never extend it.
Stock and price do not participate in applicability.
"""
from catalog.models import CarModel
from catalog.vehicle_fitment import (
    _engine_code_is_in_clause,
    _product_fitment_models,
    _product_model_years,
)


def kit_engine_years(kit, sibling_names, product_fitment):
    common = None
    for item in kit.items.all():
        product = item.product
        if kit.car_model_id not in {model.pk for model in _product_fitment_models(product)}:
            return {}
        key = (product.pk, kit.car_model_id)
        if key not in product_fitment:
            product_fitment[key] = _product_model_years(
                product, kit.car_model, sibling_names,
            )
        coverage = {
            code.upper(): set(years)
            for code, years in product_fitment[key]['engine_years'].items()
            if years and (not kit.engine or _engine_code_is_in_clause(code, kit.engine))
        }
        if common is None:
            common = coverage
        else:
            common = {
                code: years & coverage.get(code, set())
                for code, years in common.items()
                if years & coverage.get(code, set())
            }
        if not common:
            return {}

    result = {}
    for code, years in (common or {}).items():
        covered = {
            year for year in years
            if (kit.year_from is None or year >= kit.year_from)
            and (kit.year_to is None or year <= kit.year_to)
        }
        if covered:
            result[code] = covered
    return result


def build_kit_picker(kits, params):
    brands = sorted({kit.brand_id: kit.brand for kit in kits}.values(), key=lambda b: b.name.casefold())
    sibling_names = {}
    for brand_id, name in CarModel.objects.filter(
        brand_id__in=[brand.pk for brand in brands],
    ).values_list('brand_id', 'name'):
        sibling_names.setdefault(brand_id, []).append(name)

    product_fitment = {}
    coverage = {
        kit.pk: kit_engine_years(kit, sibling_names.get(kit.brand_id, ()), product_fitment)
        for kit in kits
    }
    brand_id = str(params.get('brand') or '').strip()
    model_id = str(params.get('model') or '').strip()
    year = str(params.get('year') or '').strip()
    engine = str(params.get('engine') or '').strip()
    if brand_id not in {str(brand.pk) for brand in brands}:
        brand_id, model_id, year, engine = '', '', '', ''
    brand_kits = [kit for kit in kits if str(kit.brand_id) == brand_id]
    models = sorted(
        {kit.car_model_id: kit.car_model for kit in brand_kits}.values(),
        key=lambda model: model.name.casefold(),
    )
    if model_id not in {str(model.pk) for model in models}:
        model_id, year, engine = '', '', ''
    model_kits = [kit for kit in brand_kits if str(kit.car_model_id) == model_id]
    years = sorted({
        value for kit in model_kits
        for covered in coverage[kit.pk].values() for value in covered
    }, reverse=True)
    invalid_selection = bool(year and year not in {str(value) for value in years})
    if invalid_selection:
        year, engine = '', ''
    engines = sorted({
        code for kit in model_kits for code, covered in coverage[kit.pk].items()
        if year and int(year) in covered
    })
    if engine and engine not in engines:
        invalid_selection = True
        engine = ''

    matching = model_kits if model_id else brand_kits if brand_id else kits
    if year:
        matching = [
            kit for kit in matching
            if any(
                int(year) in covered and (not engine or code == engine)
                for code, covered in coverage[kit.pk].items()
            )
        ]
    if invalid_selection:
        matching = []

    return matching, {
        'brands': brands,
        'models': models,
        'years': years,
        'engines': engines,
        'selected_brand': brand_id,
        'selected_model': model_id,
        'selected_year': year,
        'selected_engine': engine,
        'invalid_selection': invalid_selection,
        'year_not_recorded': bool(model_id and not years),
        'fitment_missing_count': sum(not coverage[kit.pk] for kit in model_kits),
    }
