"""Fixed Brand/CarModel refs needed for the AG Parts pilot.

This module never touches Product and never infers aliases
(GWM ≠ Great Wall). Matching is exact name under a concrete Brand.
"""

from dataclasses import dataclass

from catalog.models import Brand, CarModel, Country

REQUIRED_BRAND_MODELS = (
    ('Chery', ('Tiggo 4', 'Tiggo 7 Pro', 'Tiggo 8 Pro')),
    ('Exeed', ('TXL',)),
    ('Jetour', ('Dashing', 'X70', 'X90')),
    ('Zeekr', ('001', '009')),
)

VERIFY_ONLY_BRAND_MODELS = (
    ('Great Wall', ('Wingle 7',)),
)

NEW_BRAND_COUNTRY_NAME = 'Китай'

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


def _find_brands(name):
    return list(Brand.objects.filter(name=name).order_by('pk'))


def _find_model(brand, name):
    return CarModel.objects.filter(brand=brand, name=name).first()


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


def _country_for_new_brand():
    country, created = Country.objects.get_or_create(name=NEW_BRAND_COUNTRY_NAME)
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
            country, _country_created = _country_for_new_brand()
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
