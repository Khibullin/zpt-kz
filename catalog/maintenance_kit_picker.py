"""Cascading kit selection using the same product fitment as the filter finder.

The kit's own year/engine fields can narrow product coverage, never extend it.
Stock and price do not participate in applicability.
"""
from catalog.models import CarModel
from catalog.vehicle_fitment import (
    _engine_code_is_in_clause,
    _product_fitment_models,
    _product_model_years,
    _fitment_engine,
    _fitment_months,
    _reviewed_rows,
    BODY_FITMENT,
    product_selection_notices,
)


def _product_options(product, model, sibling_names):
    rows = _reviewed_rows(product)
    if rows is not None:
        options = []
        for row in rows:
            if row.status != 'confirmed' or row.car_model_id != model.pk:
                continue
            code = _fitment_engine(row)
            months = _fitment_months(row)
            if not code or not months:
                continue
            constraints = {key: str(getattr(row, key)).casefold() for key in
                           ('generation', 'body', 'market', 'fuel', 'power_kw')
                           if getattr(row, key)}
            options.append((None if code == BODY_FITMENT else code, months, constraints))
        return options
    data = _product_model_years(product, model, sibling_names)
    return [(code, {year * 12 + month for year in years for month in range(12)}, {})
            for code, years in data['engine_years'].items() if years]


def kit_engine_years(kit, sibling_names, product_fitment):
    # Intersect actual month ranges and modification constraints. Merely sharing
    # a calendar year (e.g. Jan-Apr vs Sep-Dec) cannot validate a whole kit.
    common = None
    for item in kit.items.all():
        product = item.product
        if kit.car_model_id not in {model.pk for model in _product_fitment_models(product)}:
            return {}
        key = (product.pk, kit.car_model_id)
        if key not in product_fitment:
            product_fitment[key] = _product_options(product, kit.car_model, sibling_names)
        options = [(code, months, limits) for code, months, limits in product_fitment[key]
                   if not code or not kit.engine or _engine_code_is_in_clause(code, kit.engine)]
        if common is None:
            common = options
        else:
            merged = {}
            for code, months, limits in common:
                for next_code, next_months, next_limits in options:
                    if code and next_code and code != next_code:
                        continue
                    if any(limits[key] != next_limits[key] for key in limits.keys() & next_limits.keys()):
                        continue
                    overlap = months & next_months
                    if overlap:
                        key = (code or next_code, tuple(sorted({**limits, **next_limits}.items())))
                        merged.setdefault(key, set()).update(overlap)
            common = [(key[0], months, dict(key[1])) for key, months in merged.items()]
        if not common:
            return {}

    result = {}
    for code, months, _limits in common or []:
        years = {month // 12 for month in months}
        covered = {year for year in years
                   if (kit.year_from is None or year >= kit.year_from)
                   and (kit.year_to is None or year <= kit.year_to)}
        if covered:
            result.setdefault(code or BODY_FITMENT, set()).update(covered)
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

    notices = []
    if year:
        for kit in matching:
            for item in kit.items.all():
                for note in product_selection_notices(item.product, kit.car_model, year, engine):
                    notices.append(f'{item.product.article}: {note}')

    return matching, {
        'fitment_notices': list(dict.fromkeys(notices)),
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
