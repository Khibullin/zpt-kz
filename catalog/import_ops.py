"""Catalog import helpers: SHA256, archive, shrink guard, batch persistence.

Dry-run must never call persist_import_batch or archive_import_source.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from catalog.models import CatalogImportBatch, CatalogImportItem

IMPORT_SOURCE_AG_PARTS = CatalogImportBatch.SOURCE_AG_PARTS
SCOPE_FULL = CatalogImportBatch.SCOPE_FULL
SCOPE_PARTIAL = CatalogImportBatch.SCOPE_PARTIAL
MODE_WRITE = CatalogImportBatch.MODE_WRITE
STATUS_SUCCESS = CatalogImportBatch.STATUS_SUCCESS
STATUS_BLOCKED = CatalogImportBatch.STATUS_BLOCKED
STATUS_ERROR = CatalogImportBatch.STATUS_ERROR
ARCHIVE_NOT_APPLICABLE = CatalogImportBatch.ARCHIVE_NOT_APPLICABLE
ARCHIVE_SUCCESS = CatalogImportBatch.ARCHIVE_SUCCESS
ARCHIVE_ERROR = CatalogImportBatch.ARCHIVE_ERROR


class CatalogImportWriteAborted(Exception):
    """Abort a write-import so the product transaction rolls back."""

    def __init__(self, result, message=''):
        self.result = result
        super().__init__(
            message
            or f"row error for {getattr(result, 'article', '')}: "
            f"{getattr(result, 'errors', [])}"
        )


def action_error_count(results):
    return sum(1 for item in results if getattr(item, 'action', '') == 'error')

ITEM_ACTION_MAP = {
    'created': CatalogImportItem.ACTION_CREATED,
    'updated': CatalogImportItem.ACTION_UPDATED,
    'unchanged': CatalogImportItem.ACTION_UNCHANGED,
    'skipped': CatalogImportItem.ACTION_SKIPPED,
    'conflict': CatalogImportItem.ACTION_CONFLICT,
    'legacy_ag_parts_ambiguous': CatalogImportItem.ACTION_CONFLICT,
    'error': CatalogImportItem.ACTION_ERROR,
    'missing_from_source': CatalogImportItem.ACTION_MISSING_FROM_SOURCE,
    'adopted': CatalogImportItem.ACTION_UPDATED,
    'legacy_ag_parts_match': CatalogImportItem.ACTION_SKIPPED,
    'would_adopt': CatalogImportItem.ACTION_SKIPPED,
}

LONG_DIFF_FIELDS = {
    'description',
    'compatibility',
    'engine_compatibility',
    'oem_cross_references',
}


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value) -> str:
    payload = '' if value is None else str(value)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def compact_field_diff(name, old, new):
    if old == new:
        return None
    if name in LONG_DIFF_FIELDS or (
        isinstance(old, str)
        and isinstance(new, str)
        and (len(old) + len(new) > 400)
    ):
        return {
            'old_hash': sha256_text(old),
            'new_hash': sha256_text(new),
            'old_len': len(old or ''),
            'new_len': len(new or ''),
        }
    return {'old': old, 'new': new}


def resolve_source_scope(explicit_scope, *, has_article_filter, has_limit):
    if has_article_filter or has_limit:
        if explicit_scope == SCOPE_FULL:
            raise ValueError(
                'source_scope=full нельзя сочетать с --articles или --limit. '
                'Это частичный импорт.'
            )
        return SCOPE_PARTIAL
    if explicit_scope == SCOPE_FULL:
        return SCOPE_FULL
    return SCOPE_PARTIAL


def shrink_thresholds():
    ratio = float(getattr(settings, 'CATALOG_IMPORT_SHRINK_RATIO', 0.20))
    absolute = int(getattr(settings, 'CATALOG_IMPORT_SHRINK_ABS', 10))
    return ratio, absolute


def previous_successful_full_write_batch(seller, source):
    if seller is None:
        return None
    return (
        CatalogImportBatch.objects.filter(
            seller_profile=seller,
            source=source,
            source_scope=SCOPE_FULL,
            mode=MODE_WRITE,
            status=STATUS_SUCCESS,
        )
        .order_by('-finished_at', '-id')
        .first()
    )


def previous_batch_articles(batch):
    if batch is None:
        return set()
    tracked = {
        CatalogImportItem.ACTION_CREATED,
        CatalogImportItem.ACTION_UPDATED,
        CatalogImportItem.ACTION_UNCHANGED,
    }
    return set(
        CatalogImportItem.objects.filter(
            batch=batch,
            action__in=tracked,
        ).values_list('article', flat=True)
    )


def missing_from_source_articles(previous_articles, current_articles):
    return sorted(previous_articles - set(current_articles))


def evaluate_shrink_guard(*, missing_count, previous_count, allow_shrink, shrink_reason):
    ratio_limit, abs_limit = shrink_thresholds()
    previous_count = int(previous_count or 0)
    missing_count = int(missing_count or 0)
    ratio = (missing_count / previous_count) if previous_count else 0.0
    should_block = previous_count > 0 and (
        missing_count >= abs_limit or ratio >= ratio_limit
    )
    payload = {
        'missing_count': missing_count,
        'previous_count': previous_count,
        'missing_ratio': round(ratio, 4),
        'ratio_limit': ratio_limit,
        'abs_limit': abs_limit,
        'should_block': should_block,
        'blocked': False,
        'reason': '',
    }
    if not should_block:
        return payload
    if allow_shrink:
        reason = (shrink_reason or '').strip()
        if not reason:
            raise ValueError(
                '--allow-source-shrink требует непустой --source-shrink-reason.'
            )
        payload['reason'] = reason
        return payload
    payload['blocked'] = True
    payload['reason'] = (
        f'Source shrink blocked: missing={missing_count} '
        f'({ratio:.0%} of {previous_count}); '
        f'limits ratio>={ratio_limit:.0%} or count>={abs_limit}.'
    )
    return payload


def import_archive_root() -> Path:
    configured = getattr(settings, 'IMPORT_ARCHIVE_ROOT', None)
    if configured:
        return Path(configured)
    return Path(settings.MEDIA_ROOT) / '_catalog_imports'


def archive_relative_path(dest: Path) -> str:
    """Prefer a path relative to MEDIA_ROOT so it stays on the persistent disk."""
    dest = Path(dest).resolve()
    media = Path(settings.MEDIA_ROOT).resolve()
    try:
        return dest.relative_to(media).as_posix()
    except ValueError:
        return dest.as_posix()


def archive_import_source(*, batch, source_path, report_payload, report_stem=''):
    """Copy workbook + JSON/CSV reports. Never called from dry-run."""
    root = import_archive_root()
    dest = root / batch.source / str(batch.seller_profile_id) / str(batch.pk)
    dest.mkdir(parents=True, exist_ok=True)
    source_path = Path(source_path)
    archived_source = dest / 'source.xlsx'
    shutil.copy2(source_path, archived_source)
    json_path = dest / 'report.json'
    csv_path = dest / 'report.csv'
    json_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    fieldnames = [
        'article', 'action', 'source_row', 'product_id',
        'warnings', 'errors', 'changed_fields',
    ]
    import csv

    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in report_payload:
            writer.writerow({
                'article': item.get('article', ''),
                'action': item.get('action', ''),
                'source_row': item.get('source_row', ''),
                'product_id': item.get('product_id') or '',
                'warnings': '|'.join(item.get('warnings') or []),
                'errors': '|'.join(item.get('errors') or []),
                'changed_fields': json.dumps(
                    item.get('changed_fields') or {},
                    ensure_ascii=False,
                    default=str,
                ),
            })
    batch.source_archive_path = archive_relative_path(dest)
    batch.save(update_fields=['source_archive_path'])
    return dest


def persist_import_batch(
    *,
    seller,
    source,
    filename,
    file_sha256,
    started_at,
    source_scope,
    status,
    source_row_count,
    source_unique_count,
    selected_count,
    totals,
    missing_count,
    previous_batch,
    blocked_reason='',
    shrink_reason='',
    results,
    archive_status=ARCHIVE_NOT_APPLICABLE,
    archive_error='',
):
    """Write CatalogImportBatch + items. Call only from a real write-run."""
    row_errors = action_error_count(results)
    if status == STATUS_SUCCESS and row_errors:
        raise ValueError(
            'Cannot persist a success CatalogImportBatch with action=error items'
        )
    now = timezone.now()
    batch = CatalogImportBatch.objects.create(
        seller_profile=seller,
        source=source,
        filename=filename,
        file_sha256=file_sha256,
        started_at=started_at,
        finished_at=now,
        mode=MODE_WRITE,
        source_scope=source_scope,
        status=status,
        source_row_count=source_row_count,
        source_unique_count=source_unique_count,
        selected_count=selected_count,
        created_count=totals.get('CREATED', 0),
        updated_count=totals.get('UPDATED', 0),
        unchanged_count=totals.get('UNCHANGED', 0),
        skipped_count=totals.get('SKIPPED', 0),
        conflict_count=totals.get('CONFLICT', 0),
        warning_count=totals.get('WARNING', 0),
        error_count=row_errors,
        missing_from_source_count=missing_count,
        previous_successful_batch=previous_batch,
        blocked_reason=blocked_reason or '',
        allow_source_shrink_reason=shrink_reason or '',
        archive_status=archive_status or ARCHIVE_NOT_APPLICABLE,
        archive_error=archive_error or '',
    )
    item_rows = []
    for item in results:
        action = ITEM_ACTION_MAP.get(item.action, CatalogImportItem.ACTION_ERROR)
        if item.action in {'error', 'legacy_ag_parts_ambiguous'}:
            action = (
                CatalogImportItem.ACTION_CONFLICT
                if item.action == 'legacy_ag_parts_ambiguous'
                else CatalogImportItem.ACTION_ERROR
            )
        item_rows.append(
            CatalogImportItem(
                batch=batch,
                product_id=item.product_id,
                article=item.article or '',
                action=action,
                warnings=list(item.warnings or []),
                errors=list(item.errors or []),
                changed_fields=dict(getattr(item, 'changed_fields', None) or {}),
            )
        )
    if item_rows:
        CatalogImportItem.objects.bulk_create(item_rows)
    return batch
