"""Fixed Brand/CarModel refs needed for AG Parts catalog imports.

Covers the original pilot plus every unique Brand/CarModel from the
CONFIRMED first-batch workbook columns (Марка, Модель, Дополнительные
модели). Canonical Chinese brand is Li Auto, never Lixiang. This
module never touches Product and never infers aliases
(GWM ≠ Great Wall). Matching is the canonical name under a concrete
Brand, with case/whitespace reuse of an existing row.
"""

from dataclasses import dataclass

from django.db.models import Q

from catalog.models import Brand, CarModel, Country

# Original AG Parts pilot SKUs, not present in first_batch_confirmed.xlsx.
PILOT_BRAND_MODELS = (
    ('Chery', ('Tiggo 4', 'Tiggo 7 Pro', 'Tiggo 8 Pro')),
    ('Exeed', ('TXL',)),
    ('Jetour', ('Dashing', 'X70', 'X90')),
    ('Zeekr', ('001', '009')),
)

# Unique structured pairs from first_batch_confirmed.xlsx, excluding
# Great Wall / Wingle 7 (verify-existing only, never created here).
FIRST_BATCH_CONFIRMED_BRAND_MODELS = (
    ('BYD', ('Tang',)),
    ('Changan', ('CS35', 'CS75 Plus', 'UNI-K', 'UNI-T', 'UNI-V')),
    ('Chery', (
        'Arrizo 8',
        'Tiggo 4',
        'Tiggo 7',
        'Tiggo 7 Pro',
        'Tiggo 7 Pro Max',
        'Tiggo 8',
        'Tiggo 8 Pro',
    )),
    ('Exeed', ('LX', 'TXL', 'VX')),
    ('Geely', ('Coolray',)),
    ('Great Wall', ('Poer',)),
    ('Haval', ('Dargo', 'F7', 'F7x', 'H9', 'Jolion')),
    ('JAC', ('J7', 'JS4', 'JS6', 'S3')),
    ('Jaecoo', ('J7',)),
    ('Li Auto', ('L6', 'L7', 'L8', 'L9')),
    ('Omoda', ('C5',)),
    ('Peugeot', ('308',)),
    ('Tank', ('300', '500')),
    ('Zeekr', ('X',)),
)

VERIFY_ONLY_BRAND_MODELS = (
    ('Great Wall', ('Wingle 7',)),
)


def _merge_brand_models(*groups):
    models_by_brand = {}
    for group in groups:
        for brand_name, model_names in group:
            bucket = models_by_brand.setdefault(brand_name, [])
            for model_name in model_names:
                if model_name not in bucket:
                    bucket.append(model_name)
    return tuple(
        (brand_name, tuple(sorted(models, key=str.lower)))
        for brand_name, models in sorted(
            models_by_brand.items(),
            key=lambda item: item[0].lower(),
        )
    )


REQUIRED_BRAND_MODELS = _merge_brand_models(
    PILOT_BRAND_MODELS,
    FIRST_BATCH_CONFIRMED_BRAND_MODELS,
)

NEW_BRAND_COUNTRY_NAME = 'Китай'
BRAND_COUNTRY_FALLBACK = {
    'Li Auto': 'Китай',
    'Jaecoo': 'Китай',
    'Omoda': 'Китай',
}

STATUS_EXISTS = 'EXISTS'
STATUS_WOULD_CREATE = 'WOULD_CREATE'
STATUS_CREATED = 'CREATED'
STATUS_MISSING = 'MISSING'
STATUS_AMBIGUOUS = 'AMBIGUOUS'


@dataclass(frozen=True)
class CatalogRefLine:
    status: str
    kind: str
    label: str
    create_kind: str = ''
    create_name: str = ''
    create_brand_name: str = ''

    def format(self):
        return f'{self.status} {self.kind} {self.label}'


def _canonical_lookup_names(name):
    stripped = (name or '').strip()
    collapsed = ' '.join(stripped.split())
    names = []
    for item in (stripped, collapsed):
        if item and item not in names:
            names.append(item)
    return names


def _country_name_for_brand(brand_name):
    from core.vehicle_catalog import VEHICLE_CATALOG

    for country_name, brands in VEHICLE_CATALOG.items():
        if brand_name in brands:
            return country_name
    return BRAND_COUNTRY_FALLBACK.get(brand_name, NEW_BRAND_COUNTRY_NAME)


def _find_brands(name):
    names = _canonical_lookup_names(name)
    if not names:
        return []
    found = list(Brand.objects.filter(name__in=names).order_by('pk'))
    if found:
        return found
    query = Q()
    for item in names:
        query |= Q(name__iexact=item)
    return list(Brand.objects.filter(query).order_by('pk'))


def _find_model(brand, name):
    names = _canonical_lookup_names(name)
    if not names:
        return None
    exact = CarModel.objects.filter(brand=brand, name__in=names).order_by('pk').first()
    if exact:
        return exact
    query = Q()
    for item in names:
        query |= Q(name__iexact=item)
    return CarModel.objects.filter(query, brand=brand).order_by('pk').first()


def _brand_line(status, brand_name, *, create=False):
    return CatalogRefLine(
        status=status,
        kind='Brand',
        label=brand_name,
        create_kind='brand' if create else '',
        create_name=brand_name if create else '',
    )


def _model_line(status, brand_name, model_name, *, create=False):
    return CatalogRefLine(
        status=status,
        kind='Model',
        label=f'{brand_name} / {model_name}',
        create_kind='model' if create else '',
        create_name=model_name if create else '',
        create_brand_name=brand_name if create else '',
    )


def _plan_brand_block(brand_name, model_names, *, may_create):
    lines = []
    brands = _find_brands(brand_name)
    if len(brands) > 1:
        lines.append(_brand_line(STATUS_AMBIGUOUS, brand_name))
        for model_name in model_names:
            lines.append(_model_line(STATUS_AMBIGUOUS, brand_name, model_name))
        return lines

    if not brands:
        if may_create:
            lines.append(_brand_line(STATUS_WOULD_CREATE, brand_name, create=True))
            for model_name in model_names:
                lines.append(
                    _model_line(STATUS_WOULD_CREATE, brand_name, model_name, create=True)
                )
        else:
            lines.append(_brand_line(STATUS_MISSING, brand_name))
            for model_name in model_names:
                lines.append(_model_line(STATUS_MISSING, brand_name, model_name))
        return lines

    brand = brands[0]
    lines.append(_brand_line(STATUS_EXISTS, brand_name))
    for model_name in model_names:
        if _find_model(brand, model_name):
            lines.append(_model_line(STATUS_EXISTS, brand_name, model_name))
        elif may_create:
            lines.append(
                _model_line(STATUS_WOULD_CREATE, brand_name, model_name, create=True)
            )
        else:
            lines.append(_model_line(STATUS_MISSING, brand_name, model_name))
    return lines


def plan_ag_parts_catalog_refs():
    """Read-only plan. Never writes."""
    lines = []
    for brand_name, model_names in VERIFY_ONLY_BRAND_MODELS:
        lines.extend(_plan_brand_block(brand_name, model_names, may_create=False))
    for brand_name, model_names in REQUIRED_BRAND_MODELS:
        lines.extend(_plan_brand_block(brand_name, model_names, may_create=True))
    return lines


def _country_for_new_brand(brand_name):
    country, created = Country.objects.get_or_create(
        name=_country_name_for_brand(brand_name),
    )
    return country, created


def apply_ag_parts_catalog_refs(plan_lines):
    """Create only planned Brand/CarModel rows. Idempotent."""
    applied = []
    created_brands = {}

    for line in plan_lines:
        if line.status != STATUS_WOULD_CREATE:
            applied.append(line)
            continue

        if line.create_kind == 'brand':
            existing = _find_brands(line.create_name)
            if len(existing) == 1:
                created_brands[line.create_name] = existing[0]
                applied.append(_brand_line(STATUS_EXISTS, line.create_name))
                continue
            if existing:
                applied.append(_brand_line(STATUS_AMBIGUOUS, line.create_name))
                continue
            country, _country_created = _country_for_new_brand(line.create_name)
            brand, brand_created = Brand.objects.get_or_create(
                country=country,
                name=line.create_name,
            )
            created_brands[line.create_name] = brand
            applied.append(_brand_line(
                STATUS_CREATED if brand_created else STATUS_EXISTS,
                line.create_name,
            ))
            continue

        if line.create_kind == 'model':
            brand_name = line.create_brand_name
            brand = created_brands.get(brand_name)
            if brand is None:
                matches = _find_brands(brand_name)
                brand = matches[0] if len(matches) == 1 else None
            if brand is None:
                applied.append(_model_line(STATUS_MISSING, brand_name, line.create_name))
                continue
            model, model_created = CarModel.objects.get_or_create(
                brand=brand,
                name=line.create_name,
            )
            applied.append(_model_line(
                STATUS_CREATED if model_created else STATUS_EXISTS,
                brand_name,
                line.create_name,
            ))
            continue

        applied.append(line)

    return applied


def ensure_ag_parts_catalog_refs(*, apply=False):
    plan = plan_ag_parts_catalog_refs()
    if not apply:
        return plan
    return apply_ag_parts_catalog_refs(plan)
