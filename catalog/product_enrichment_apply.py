import csv
import json
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from catalog.models import Brand, Category, Product
from catalog.product_quality import detect_internal_research_text


APPLYABLE_FIELDS = (
    'title',
    'brand',
    'category',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
    'description',
)

STATUS_READY = 'READY'
STATUS_CHANGED = 'CHANGED'
STATUS_UNCHANGED = 'UNCHANGED'
STATUS_ALREADY_APPLIED = 'ALREADY_APPLIED'
STATUS_STALE = 'STALE_PRODUCT'
STATUS_ERROR = 'ERROR'

APPLY_CSV_COLUMNS = (
    'product_id',
    'article',
    'status',
    'changed_fields',
    'skipped_fields',
    'before',
    'after',
    'errors',
)

TEXT_PRODUCT_ATTRS = {
    'title': 'title',
    'compatibility': 'compatibility',
    'engine_compatibility': 'engine_compatibility',
    'oem_cross_references': 'oem_cross_references',
    'description': 'description',
}


class ApplySnapshotError(ValueError):
    """Invalid reviewed snapshot file."""


def _norm_text(value) -> str:
    return ' '.join(str(value or '').split())


def _norm_id(value):
    if value in (None, '', False):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _json_ready(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return str(value)


def load_preview_snapshot(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ApplySnapshotError(f'Preview-файл не найден: {path}') from exc
    except OSError as exc:
        raise ApplySnapshotError(f'Не удалось прочитать preview-файл: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ApplySnapshotError(f'Некорректный JSON snapshot: {path}') from exc
    if not isinstance(payload, dict) or not isinstance(payload.get('products'), list):
        raise ApplySnapshotError('Snapshot должен содержать список products.')
    return payload


def index_snapshot_products(payload: dict) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for row in payload.get('products') or []:
        if not isinstance(row, dict):
            continue
        product_id = _norm_id(row.get('product_id'))
        if product_id is None:
            continue
        indexed[product_id] = row
    return indexed


def _snapshot_article(row: dict) -> str:
    return _norm_text(row.get('current_article') or row.get('article') or '')


def _fields(row: dict) -> dict:
    data = row.get('fields')
    return data if isinstance(data, dict) else {}


def snapshot_current_values(row: dict) -> dict:
    return {
        'title': row.get('current_title') or '',
        'brand': row.get('current_brand') or '',
        'brand_id': _norm_id(row.get('current_brand_id')),
        'category': row.get('current_category') or '',
        'category_id': _norm_id(row.get('current_category_id')),
        'compatibility': row.get('current_compatibility') or '',
        'engine_compatibility': row.get('current_engine_compatibility') or '',
        'oem_cross_references': row.get('current_oem_cross_references') or '',
        'description': row.get('current_description') or '',
        'article': _snapshot_article(row),
    }


def snapshot_proposed_values(row: dict) -> dict:
    fields = _fields(row)
    brand_id = row.get('suggested_brand_id')
    if brand_id in (None, ''):
        brand_id = fields.get('brand_id')
    category_id = row.get('suggested_category_id')
    if category_id in (None, ''):
        category_id = fields.get('category_id')
    return {
        'title': row.get('suggested_title') if row.get('suggested_title') not in (None, '') else (fields.get('title') or ''),
        'brand': row.get('suggested_brand') if row.get('suggested_brand') not in (None, '') else (fields.get('brand_name') or ''),
        'brand_id': _norm_id(brand_id),
        'category': row.get('suggested_category') if row.get('suggested_category') not in (None, '') else (fields.get('category_name') or ''),
        'category_id': _norm_id(category_id),
        'compatibility': (
            row.get('suggested_compatibility')
            if row.get('suggested_compatibility') not in (None, '')
            else (fields.get('compatibility') or '')
        ),
        'engine_compatibility': (
            row.get('suggested_engine_compatibility')
            if row.get('suggested_engine_compatibility') not in (None, '')
            else (fields.get('engine_compatibility') or '')
        ),
        'oem_cross_references': (
            row.get('suggested_oem_cross_references')
            if row.get('suggested_oem_cross_references') not in (None, '')
            else (fields.get('oem_cross_references') or '')
        ),
        'description': (
            row.get('suggested_description')
            if row.get('suggested_description') not in (None, '')
            else (fields.get('description') or '')
        ),
    }


def live_values(product: Product) -> dict:
    brand_name = product.brand.name if product.brand_id and product.brand else ''
    category_name = product.category.name if product.category_id and product.category else ''
    return {
        'title': product.title or '',
        'brand': brand_name,
        'brand_id': product.brand_id,
        'category': category_name,
        'category_id': product.category_id,
        'compatibility': product.compatibility or '',
        'engine_compatibility': product.engine_compatibility or '',
        'oem_cross_references': product.oem_cross_references or '',
        'description': product.description or '',
        'article': product.article or '',
    }


def public_field_values(values: dict) -> dict:
    return {
        'title': values.get('title') or '',
        'brand': values.get('brand') or '',
        'brand_id': values.get('brand_id'),
        'category': values.get('category') or '',
        'category_id': values.get('category_id'),
        'compatibility': values.get('compatibility') or '',
        'engine_compatibility': values.get('engine_compatibility') or '',
        'oem_cross_references': values.get('oem_cross_references') or '',
        'description': values.get('description') or '',
    }


def approved_field_names(row: dict) -> list[str]:
    blocked = {
        str(item).strip()
        for item in (row.get('blocked_fields') or [])
        if str(item).strip()
    }
    decisions = row.get('field_decisions') if isinstance(row.get('field_decisions'), dict) else {}
    names: list[str] = []
    seen: set[str] = set()
    for raw in row.get('approved_fields') or []:
        name = str(raw or '').strip()
        if name not in APPLYABLE_FIELDS or name in seen:
            continue
        if name in blocked:
            continue
        decision = str(decisions.get(name) or '').strip()
        if decision and decision != 'approved':
            continue
        names.append(name)
        seen.add(name)
    return names


def _text_equal(left, right) -> bool:
    return _norm_text(left) == _norm_text(right)


def _fk_equal(live_id, live_name, other_id, other_name) -> bool:
    live_id = _norm_id(live_id)
    other_id = _norm_id(other_id)
    if other_id is not None:
        return live_id == other_id
    return _norm_text(live_name).casefold() == _norm_text(other_name).casefold()


def _brand_equal(live: dict, other_id, other_name) -> bool:
    return _fk_equal(live.get('brand_id'), live.get('brand'), other_id, other_name)


def _category_equal(live: dict, other_id, other_name) -> bool:
    return _fk_equal(live.get('category_id'), live.get('category'), other_id, other_name)


def field_matches_proposed(name: str, live: dict, proposed: dict) -> bool:
    if name == 'brand':
        return _brand_equal(live, proposed.get('brand_id'), proposed.get('brand'))
    if name == 'category':
        return _category_equal(live, proposed.get('category_id'), proposed.get('category'))
    return _text_equal(live.get(name), proposed.get(name))


def field_matches_current(name: str, live: dict, current: dict) -> bool:
    if name == 'brand':
        return _brand_equal(live, current.get('brand_id'), current.get('brand'))
    if name == 'category':
        return _category_equal(live, current.get('category_id'), current.get('category'))
    return _text_equal(live.get(name), current.get(name))


def resolve_existing_brand(proposed: dict) -> Brand:
    brand_id = _norm_id(proposed.get('brand_id'))
    name = _norm_text(proposed.get('brand'))
    if brand_id is not None:
        brand = Brand.objects.filter(pk=brand_id).first()
        if brand is None:
            raise LookupError(f'Brand id={brand_id} не найден')
        if name and brand.name.strip().casefold() != name.casefold():
            raise LookupError(
                f'Brand id={brand_id} не совпадает с именем «{name}»'
            )
        return brand
    if not name:
        raise LookupError('В snapshot нет brand_id/имени для approved brand')
    matches = list(Brand.objects.filter(name__iexact=name).order_by('id')[:2])
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise LookupError(f'Марка «{name}» неоднозначна')
    raise LookupError(f'Марка «{name}» не найдена в справочнике')


def resolve_existing_category(proposed: dict) -> Category:
    category_id = _norm_id(proposed.get('category_id'))
    name = _norm_text(proposed.get('category'))
    if category_id is not None:
        category = Category.objects.filter(pk=category_id).first()
        if category is None:
            raise LookupError(f'Category id={category_id} не найден')
        if name and category.name.strip().casefold() != name.casefold():
            raise LookupError(
                f'Category id={category_id} не совпадает с именем «{name}»'
            )
        return category
    if not name:
        raise LookupError('В snapshot нет category_id/имени для approved category')
    matches = list(Category.objects.filter(name__iexact=name).order_by('id')[:2])
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise LookupError(f'Категория «{name}» неоднозначна')
    raise LookupError(f'Категория «{name}» не найдена в справочнике')


def _empty_result(product_id: int, article: str, status: str, errors: list[str]) -> dict:
    return {
        'product_id': product_id,
        'article': article,
        'status': status,
        'changed_fields': [],
        'skipped_fields': [],
        'before': {},
        'after': {},
        'errors': errors,
        'updates': {},
        'planned': False,
    }


def plan_product_apply(*, product_id: int, row: dict | None, product: Product | None) -> dict:
    if row is None:
        return _empty_result(product_id, '', STATUS_ERROR, ['нет в snapshot'])
    article = _snapshot_article(row)
    if product is None:
        return _empty_result(product_id, article, STATUS_ERROR, ['Product не найден'])

    live = live_values(product)
    before = public_field_values(live)
    if _norm_text(live.get('article')) != article:
        return {
            **_empty_result(
                product_id,
                article,
                STATUS_ERROR,
                [f'article mismatch: snapshot={article!r} db={live.get("article")!r}'],
            ),
            'before': before,
            'after': before,
        }

    approved = approved_field_names(row)
    proposed = snapshot_proposed_values(row)
    current = snapshot_current_values(row)
    skipped: list[str] = []
    errors: list[str] = []

    if not approved:
        return {
            'product_id': product_id,
            'article': article,
            'status': STATUS_UNCHANGED,
            'changed_fields': [],
            'skipped_fields': [],
            'before': before,
            'after': before,
            'errors': [],
            'updates': {},
            'planned': False,
        }

    if all(field_matches_proposed(name, live, proposed) for name in approved):
        return {
            'product_id': product_id,
            'article': article,
            'status': STATUS_ALREADY_APPLIED,
            'changed_fields': [],
            'skipped_fields': list(approved),
            'before': before,
            'after': before,
            'errors': [],
            'updates': {},
            'planned': False,
        }

    stale_fields = [
        name for name in APPLYABLE_FIELDS
        if not field_matches_current(name, live, current)
    ]
    if stale_fields:
        return {
            'product_id': product_id,
            'article': article,
            'status': STATUS_STALE,
            'changed_fields': [],
            'skipped_fields': list(approved),
            'before': before,
            'after': before,
            'errors': [f'STALE_PRODUCT: changed since preview: {", ".join(stale_fields)}'],
            'updates': {},
            'planned': False,
        }

    updates: dict = {}
    after = dict(before)
    for name in approved:
        if field_matches_proposed(name, live, proposed):
            skipped.append(name)
            continue
        if name == 'brand':
            try:
                brand = resolve_existing_brand(proposed)
            except LookupError as exc:
                return {
                    'product_id': product_id,
                    'article': article,
                    'status': STATUS_ERROR,
                    'changed_fields': [],
                    'skipped_fields': list(approved),
                    'before': before,
                    'after': before,
                    'errors': [str(exc)],
                    'updates': {},
                    'planned': False,
                }
            updates['brand'] = brand
            after['brand'] = brand.name
            after['brand_id'] = brand.pk
            continue
        if name == 'category':
            try:
                category = resolve_existing_category(proposed)
            except LookupError as exc:
                return {
                    'product_id': product_id,
                    'article': article,
                    'status': STATUS_ERROR,
                    'changed_fields': [],
                    'skipped_fields': list(approved),
                    'before': before,
                    'after': before,
                    'errors': [str(exc)],
                    'updates': {},
                    'planned': False,
                }
            updates['category'] = category
            after['category'] = category.name
            after['category_id'] = category.pk
            continue
        text = proposed.get(name) or ''
        if not _norm_text(text):
            errors.append(f'{name}: пустое approved-значение')
            skipped.append(name)
            continue
        if detect_internal_research_text(text):
            errors.append(f'{name}: внутренний research-текст не записывается')
            skipped.append(name)
            continue
        updates[name] = text
        after[name] = text

    if not updates:
        return {
            'product_id': product_id,
            'article': article,
            'status': STATUS_ERROR if errors else STATUS_UNCHANGED,
            'changed_fields': [],
            'skipped_fields': skipped,
            'before': before,
            'after': before,
            'errors': errors,
            'updates': {},
            'planned': False,
        }

    return {
        'product_id': product_id,
        'article': article,
        'status': STATUS_READY,
        'changed_fields': [name for name in approved if name in updates],
        'skipped_fields': skipped,
        'before': before,
        'after': after,
        'errors': errors,
        'updates': updates,
        'planned': True,
    }


def _apply_plan(product: Product, plan: dict) -> None:
    updates = plan.get('updates') or {}
    update_fields: list[str] = []
    for name, value in updates.items():
        if name == 'brand':
            product.brand = value
            update_fields.append('brand')
            continue
        if name == 'category':
            product.category = value
            update_fields.append('category')
            continue
        attr = TEXT_PRODUCT_ATTRS.get(name)
        if not attr:
            continue
        setattr(product, attr, value)
        update_fields.append(attr)
    if not update_fields:
        return
    product.save(update_fields=update_fields)


def summarize_apply_results(results: list[dict], *, apply: bool) -> dict:
    ready = 0
    changed = 0
    unchanged = 0
    stale = 0
    errors = 0
    for item in results:
        status = item.get('status')
        if item.get('planned') or status == STATUS_READY:
            ready += 1
        if status == STATUS_CHANGED:
            changed += 1
            if not item.get('planned'):
                ready += 1
        elif status in {STATUS_UNCHANGED, STATUS_ALREADY_APPLIED}:
            unchanged += 1
        elif status == STATUS_STALE:
            stale += 1
        elif status == STATUS_ERROR:
            errors += 1
    if apply:
        ready = sum(1 for item in results if item.get('status') == STATUS_CHANGED)
    return {
        'total': len(results),
        'ready': ready,
        'changed': changed,
        'unchanged': unchanged,
        'stale': stale,
        'errors': errors,
        'mode': 'apply' if apply else 'dry-run',
    }


def write_apply_reports(
    results: list[dict],
    *,
    report_dir: Path,
    stem: str,
    summary: dict,
    preview_file: str,
):
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f'{stem}.csv'
    json_path = report_dir / f'{stem}.json'
    public_rows = []
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=APPLY_CSV_COLUMNS)
        writer.writeheader()
        for item in results:
            public = {
                'product_id': item.get('product_id'),
                'article': item.get('article') or '',
                'status': item.get('status') or '',
                'changed_fields': list(item.get('changed_fields') or []),
                'skipped_fields': list(item.get('skipped_fields') or []),
                'before': _json_ready(item.get('before') or {}),
                'after': _json_ready(item.get('after') or {}),
                'errors': list(item.get('errors') or []),
            }
            public_rows.append(public)
            writer.writerow({
                'product_id': public['product_id'] or '',
                'article': public['article'],
                'status': public['status'],
                'changed_fields': ', '.join(public['changed_fields']),
                'skipped_fields': ', '.join(public['skipped_fields']),
                'before': json.dumps(public['before'], ensure_ascii=False),
                'after': json.dumps(public['after'], ensure_ascii=False),
                'errors': ' | '.join(public['errors']),
            })
    payload = {
        'generated_at': timezone.now().isoformat(),
        'preview_file': preview_file,
        'summary': summary,
        'products': public_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return csv_path, json_path


def apply_preview_snapshot(
    *,
    preview_file: Path,
    product_ids: list[int],
    apply: bool = False,
    report_dir: Path,
    report_stem: str = '',
) -> dict:
    payload = load_preview_snapshot(preview_file)
    indexed = index_snapshot_products(payload)
    products = {
        item.pk: item
        for item in Product.objects.filter(pk__in=product_ids).select_related(
            'brand',
            'category',
            'car_model',
            'seller_profile',
        )
    }
    results = [
        plan_product_apply(
            product_id=product_id,
            row=indexed.get(product_id),
            product=products.get(product_id),
        )
        for product_id in product_ids
    ]
    if apply:
        with transaction.atomic():
            for item in results:
                if item.get('status') != STATUS_READY:
                    continue
                product = products.get(item['product_id'])
                if product is None:
                    raise RuntimeError(f'Product {item["product_id"]} исчез во время apply')
                _apply_plan(product, item)
                item['status'] = STATUS_CHANGED
    summary = summarize_apply_results(results, apply=apply)
    stem = report_stem or f'{preview_file.stem}_apply'
    csv_path, json_path = write_apply_reports(
        results,
        report_dir=report_dir,
        stem=stem,
        summary=summary,
        preview_file=str(preview_file),
    )
    return {
        'results': results,
        'summary': summary,
        'csv_path': csv_path,
        'json_path': json_path,
    }
