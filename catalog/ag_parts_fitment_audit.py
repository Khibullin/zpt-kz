"""AG Parts fitment audit: verified text/selected_models patches.

Dry-run by default. Never writes price, stock, photos, PP1/PP2, or article.
Slug is rewritten only when canonical_slug is in FITMENT_SLUG_REDIRECTS and
the current slug is the mapped old value. Matches Product by exact article
+ AG Parts seller, count == 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.db.models import Q

from catalog.fitment_slug_redirects import FITMENT_SLUG_REDIRECTS
from catalog.models import Brand, CarModel, Product, ProductKaspiListing


SELLER_NAME = 'AG Parts'
DATA_DIR = Path(__file__).resolve().parent / 'data' / 'ag_parts_fitment_audit'

TEXT_FIELDS = (
    'title',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
    'description',
)

PROTECTED_ATTRS = (
    'price',
    'stock_qty',
    'cost_price',
    'slug',
    'article',
    'seller_name',
    'status',
    'main_image',
    'publish_to_kaspi',
)

STATUS_WOULD_CHANGE = 'WOULD_CHANGE'
STATUS_CHANGED = 'CHANGED'
STATUS_UNCHANGED = 'UNCHANGED'
STATUS_ERROR = 'ERROR'
STATUS_MISSING = 'MISSING_PRODUCT'
STATUS_DUPLICATE = 'DUPLICATE_PRODUCT'


class FitmentAuditError(ValueError):
    """Invalid batch spec or unresolved catalog ref."""


@dataclass
class FitmentPlan:
    article: str
    product: Product | None = None
    status: str = STATUS_UNCHANGED
    errors: list[str] = field(default_factory=list)
    changed_fields: list[str] = field(default_factory=list)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    text_updates: dict = field(default_factory=dict)
    primary_model: CarModel | None = None
    selected_models: list[CarModel] | None = None
    update_primary: bool = False
    update_selected: bool = False
    new_slug: str = ''


def batch_path(batch_id: str) -> Path:
    name = str(batch_id or '').strip()
    if not name:
        raise FitmentAuditError('Укажите номер партии.')
    path = DATA_DIR / f'batch_{name}.json'
    if not path.is_file():
        raise FitmentAuditError(f'Файл партии не найден: {path}')
    return path


def load_batch(batch_id: str) -> dict:
    path = batch_path(batch_id)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise FitmentAuditError(f'Некорректный JSON партии: {path}') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('articles'), list):
        raise FitmentAuditError('Партия должна содержать список articles.')
    return payload


def ag_parts_products():
    return Product.objects.filter(
        Q(seller_name__iexact=SELLER_NAME)
        | Q(seller_profile__name__iexact=SELLER_NAME)
    ).select_related(
        'brand',
        'car_model',
        'car_model__brand',
        'seller_profile',
        'category',
    ).prefetch_related(
        'selected_brands',
        'selected_models',
        'selected_models__brand',
        'kaspi_listings',
        'maintenance_kit_items__kit',
    )


def resolve_ag_parts_product(article: str) -> tuple[Product | None, str]:
    qs = list(
        ag_parts_products().filter(article__iexact=str(article or '').strip())
    )
    if not qs:
        return None, STATUS_MISSING
    if len(qs) > 1:
        return None, STATUS_DUPLICATE
    return qs[0], ''


def resolve_car_model(brand_name: str, model_name: str) -> CarModel:
    brand_name = str(brand_name or '').strip()
    model_name = str(model_name or '').strip()
    if not brand_name or not model_name:
        raise FitmentAuditError('Нужны brand и model.')
    brands = list(Brand.objects.filter(name__iexact=brand_name))
    if len(brands) != 1:
        raise FitmentAuditError(
            f'Марка {brand_name!r}: найдено {len(brands)}, нужна ровно одна.'
        )
    models = list(
        CarModel.objects.filter(brand=brands[0], name__iexact=model_name)
    )
    if len(models) != 1:
        raise FitmentAuditError(
            f'{brand_name} {model_name}: найдено {len(models)} моделей, нужна одна.'
        )
    return models[0]


def _model_pairs(product: Product) -> list[dict]:
    rows = []
    for model in product.selected_models.all().select_related('brand'):
        rows.append({
            'brand': model.brand.name if model.brand_id else '',
            'model': model.name,
            'id': model.pk,
        })
    return sorted(rows, key=lambda item: (item['brand'], item['model']))


def _protected_snapshot(product: Product) -> dict:
    return {
        'price': product.price,
        'stock_qty': product.stock_qty,
        'cost_price': product.cost_price,
        'slug': product.slug,
        'article': product.article,
        'seller_name': product.seller_name,
        'status': product.status,
        'main_image': str(product.main_image or ''),
        'publish_to_kaspi': product.publish_to_kaspi,
    }


def snapshot_product(product: Product) -> dict:
    primary = None
    if product.car_model_id:
        primary = {
            'brand': product.car_model.brand.name if product.car_model.brand_id else '',
            'model': product.car_model.name,
            'id': product.car_model_id,
        }
    protected = _protected_snapshot(product)
    return {
        'product_id': product.pk,
        'slug': product.slug,
        'url': product.get_absolute_url() if product.slug else '',
        'primary_model': primary,
        'selected_models': _model_pairs(product),
        'protected': protected,
        **{name: getattr(product, name) or '' for name in TEXT_FIELDS},
    }


def _norm(value) -> str:
    return ' '.join(str(value or '').split())


def _parse_model_ref(raw) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise FitmentAuditError('Модель должна быть объектом {brand, model}.')
    return str(raw.get('brand') or '').strip(), str(raw.get('model') or '').strip()


def plan_fitment_batch(spec: dict) -> list[FitmentPlan]:
    plans: list[FitmentPlan] = []
    for row in spec.get('articles') or []:
        if not isinstance(row, dict):
            plans.append(FitmentPlan(article='', status=STATUS_ERROR, errors=['Строка партии не объект.']))
            continue
        article = str(row.get('article') or '').strip()
        plan = FitmentPlan(article=article)
        if not article:
            plan.status = STATUS_ERROR
            plan.errors.append('Пустой артикул.')
            plans.append(plan)
            continue
        product, fail = resolve_ag_parts_product(article)
        if fail:
            plan.status = fail
            plan.errors.append(
                'AG Parts товар не найден.'
                if fail == STATUS_MISSING
                else f'Несколько AG Parts товаров с артикулом {article}.'
            )
            plans.append(plan)
            continue
        plan.product = product
        plan.before = snapshot_product(product)
        try:
            fields = row.get('fields') if isinstance(row.get('fields'), dict) else {}
            for name in TEXT_FIELDS:
                if name not in fields:
                    continue
                new_value = fields[name]
                if new_value is None:
                    new_value = ''
                if not isinstance(new_value, str):
                    raise FitmentAuditError(f'{article}: поле {name} должно быть строкой.')
                if _norm(getattr(product, name)) != _norm(new_value):
                    plan.text_updates[name] = new_value
            if 'primary_model' in row:
                plan.update_primary = True
                raw_primary = row.get('primary_model')
                if raw_primary:
                    brand_name, model_name = _parse_model_ref(raw_primary)
                    plan.primary_model = resolve_car_model(brand_name, model_name)
            if 'selected_models' in row:
                plan.update_selected = True
                raw_models = row.get('selected_models')
                if raw_models is None:
                    raw_models = []
                if not isinstance(raw_models, list):
                    raise FitmentAuditError(f'{article}: selected_models должен быть списком.')
                selected = []
                seen = set()
                for item in raw_models:
                    brand_name, model_name = _parse_model_ref(item)
                    model = resolve_car_model(brand_name, model_name)
                    if model.pk not in seen:
                        seen.add(model.pk)
                        selected.append(model)
                plan.selected_models = selected
            if 'canonical_slug' in row:
                wanted = str(row.get('canonical_slug') or '').strip()
                if not wanted:
                    raise FitmentAuditError(f'{article}: пустой canonical_slug.')
                old_for_wanted = {
                    new: old for old, new in FITMENT_SLUG_REDIRECTS.items()
                }.get(wanted)
                if not old_for_wanted:
                    raise FitmentAuditError(
                        f'{article}: slug {wanted} нет в FITMENT_SLUG_REDIRECTS.'
                    )
                if product.slug not in (wanted, old_for_wanted):
                    raise FitmentAuditError(
                        f'{article}: текущий slug {product.slug} не связан с {wanted}.'
                    )
                if product.slug != wanted:
                    if Product.objects.filter(slug=wanted).exclude(pk=product.pk).exists():
                        raise FitmentAuditError(
                            f'{article}: slug {wanted} уже занят другим товаром.'
                        )
                    plan.new_slug = wanted
        except FitmentAuditError as exc:
            plan.status = STATUS_ERROR
            plan.errors.append(str(exc))
            plans.append(plan)
            continue

        after = dict(plan.before)
        if plan.text_updates:
            after.update(plan.text_updates)
            plan.changed_fields.extend(plan.text_updates.keys())
        if plan.update_primary:
            new_primary = None
            if plan.primary_model is not None:
                new_primary = {
                    'brand': plan.primary_model.brand.name,
                    'model': plan.primary_model.name,
                    'id': plan.primary_model.pk,
                }
            if (plan.before.get('primary_model') or {}) != (new_primary or {}):
                after['primary_model'] = new_primary
                plan.changed_fields.append('car_model')
        if plan.update_selected:
            new_selected = sorted(
                [
                    {
                        'brand': model.brand.name,
                        'model': model.name,
                        'id': model.pk,
                    }
                    for model in (plan.selected_models or [])
                ],
                key=lambda item: (item['brand'], item['model']),
            )
            if plan.before.get('selected_models') != new_selected:
                after['selected_models'] = new_selected
                plan.changed_fields.append('selected_models')
        if plan.new_slug:
            after['slug'] = plan.new_slug
            plan.changed_fields.append('slug')
        plan.after = after
        plan.changed_fields = list(dict.fromkeys(plan.changed_fields))
        plan.status = STATUS_WOULD_CHANGE if plan.changed_fields else STATUS_UNCHANGED
        plans.append(plan)
    return plans


def apply_fitment_plans(plans: list[FitmentPlan], *, apply: bool) -> list[FitmentPlan]:
    if not apply:
        return plans
    with transaction.atomic():
        for plan in plans:
            if plan.status != STATUS_WOULD_CHANGE or plan.product is None:
                continue
            product = Product.objects.select_for_update().get(pk=plan.product.pk)
            protected_before = _protected_snapshot(product)
            update_fields = []
            for name, value in plan.text_updates.items():
                setattr(product, name, value)
                update_fields.append(name)
            if plan.update_primary:
                product.car_model = plan.primary_model
                update_fields.append('car_model')
            if plan.new_slug:
                product.slug = plan.new_slug
                update_fields.append('slug')
            if update_fields:
                product.save(update_fields=update_fields)
            if plan.update_selected:
                models = plan.selected_models or []
                product.selected_models.set(models)
                brands = {model.brand for model in models if model.brand_id}
                if product.brand_id:
                    brands.add(product.brand)
                product.selected_brands.set(brands)
            product.refresh_from_db()
            protected_after = _protected_snapshot(product)
            ignore = {'slug'} if plan.new_slug else set()
            protected_before_cmp = {
                key: value for key, value in protected_before.items() if key not in ignore
            }
            protected_after_cmp = {
                key: value for key, value in protected_after.items() if key not in ignore
            }
            if protected_before_cmp != protected_after_cmp:
                raise FitmentAuditError(
                    f'{plan.article}: запрещённые поля изменились, откат.'
                )
            plan.product = product
            plan.after = snapshot_product(product)
            plan.status = STATUS_CHANGED
    return plans


def format_plan_report(plans: list[FitmentPlan], *, apply: bool) -> str:
    mode = 'apply' if apply else 'dry-run'
    lines = [f'mode {mode}', f'total {len(plans)}']
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.status] = counts.get(plan.status, 0) + 1
        extra = ''
        if plan.product is not None:
            extra = f' product_id={plan.product.pk} slug={plan.product.slug}'
        lines.append(f'{plan.article or "-"} {plan.status}{extra}')
        if plan.changed_fields:
            lines.append('  fields: ' + ', '.join(plan.changed_fields))
        for error in plan.errors:
            lines.append(f'  error: {error}')
    lines.append('SUMMARY:')
    for key in (
        STATUS_WOULD_CHANGE,
        STATUS_CHANGED,
        STATUS_UNCHANGED,
        STATUS_ERROR,
        STATUS_MISSING,
        STATUS_DUPLICATE,
    ):
        lines.append(f'{key} {counts.get(key, 0)}')
    return '\n'.join(lines)


def dump_ag_parts_cards() -> dict:
    products = list(ag_parts_products().order_by('article', 'id'))
    rows = []
    for product in products:
        snap = snapshot_product(product)
        listings = []
        for listing in product.kaspi_listings.all():
            listings.append({
                'listing_id': listing.pk,
                'master_sku': listing.master_sku,
                'merchant_sku': listing.merchant_sku,
                'public_url': listing.public_url,
                'is_active': listing.is_active,
                'publish_to_kaspi': listing.publish_to_kaspi,
            })
        kits = []
        for item in product.maintenance_kit_items.all():
            kits.append({
                'kit_id': item.kit_id,
                'slug': item.kit.slug,
                'name': item.kit.name,
                'is_active': item.kit.is_active,
                'quantity': item.quantity,
            })
        rows.append({
            **snap,
            'article': product.article,
            'kaspi_listings': listings,
            'maintenance_kits': kits,
        })
    return {
        'seller': SELLER_NAME,
        'count': len(rows),
        'products': rows,
    }
