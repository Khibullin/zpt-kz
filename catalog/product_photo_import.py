"""Seller-scoped bulk product photo import from a ZIP archive (admin-only).

Preview persists CatalogImportBatch dry-run rows and stores the ZIP via
default_storage. Apply requires POST + confirm and does not create products.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath

from django import forms
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from catalog.ag_parts_air_filters import (
    AG_PARTS_SLUG,
    APPROVED_AIR_FILTER_ARTICLES,
    article_key,
    resolve_photo_article,
)
from catalog.models import (
    CatalogImportBatch,
    CatalogImportItem,
    Product,
    ProductImage,
)

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_FILE_COUNT = 500
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
ALLOWED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.avif'}
SKIP_DIR_NAMES = {'__macosx', 'thumbs.db'}
SKIP_FILE_NAMES = {'.ds_store', 'thumbs.db', 'desktop.ini'}

STALE_APPLY_MESSAGE = (
    'Данные изменились после предварительной проверки. '
    'Создайте новый preview.'
)


class PhotoImportUploadForm(forms.Form):
    file = forms.FileField(label='Файл ZIP')

    def clean_file(self):
        uploaded = self.cleaned_data['file']
        name = str(getattr(uploaded, 'name', '') or '')
        if not name.lower().endswith('.zip'):
            raise forms.ValidationError('Нужен файл в формате .zip.')
        size = getattr(uploaded, 'size', None)
        if size is not None and size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError('Файл больше 80 МБ.')
        header = uploaded.read(4)
        uploaded.seek(0)
        if header[:2] != b'PK':
            raise forms.ValidationError('Файл не похож на ZIP.')
        if size is None:
            payload = uploaded.read()
            uploaded.seek(0)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise forms.ValidationError('Файл больше 80 МБ.')
        return uploaded


class PhotoImportError(Exception):
    pass


class PhotoImportStaleError(PhotoImportError):
    pass


class PhotoImportBlockedError(PhotoImportError):
    pass


@dataclass
class PhotoFile:
    name: str
    data: bytes
    digest: str
    zip_name: str = ''


@dataclass
class PhotoImportRow:
    folder_name: str
    article: str
    action: str
    display_status: str
    product: Product | None = None
    alias_used: str = ''
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    existing_names: list = field(default_factory=list)
    existing_hashes: list = field(default_factory=list)
    baseline: dict = field(default_factory=dict)
    changed_fields: dict = field(default_factory=dict)

    @property
    def photo_count(self):
        return len(self.photos)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_upload_filename(name: str) -> str:
    base = PurePosixPath(str(name or '').replace('\\', '/')).name
    base = re.sub(r'[^\w.\-]+', '_', base, flags=re.UNICODE).strip('._') or 'upload.zip'
    if not base.lower().endswith('.zip'):
        base = f'{base}.zip'
    return base[:255]


def seller_photo_whitelist(seller):
    if getattr(seller, 'slug', '') == AG_PARTS_SLUG:
        return APPROVED_AIR_FILTER_ARTICLES
    return None


def _inspect_zip_member(name):
    normalized = (name or '').replace('\\', '/')
    if not normalized or normalized in {'.', '/'}:
        return None, 'empty_path'
    if normalized.startswith('/') or normalized.startswith('\\'):
        return None, 'absolute_path'
    if re.match(r'^[A-Za-z]:(/|$)', normalized):
        return None, 'absolute_path'
    parts = []
    for part in normalized.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            return None, 'path_traversal'
        if ':' in part:
            return None, 'absolute_path'
        parts.append(part)
    if not parts:
        return None, 'empty_path'
    return parts, ''


def _is_skipped_dir(part):
    return part.casefold() in SKIP_DIR_NAMES or part.startswith('._')


def _is_skipped_file(name):
    lowered = name.casefold()
    if lowered in SKIP_FILE_NAMES:
        return True
    if name.startswith('._'):
        return True
    return False


def safe_image_filename(name):
    base = PurePosixPath(str(name or '').replace('\\', '/')).name
    base = re.sub(r'[^\w.\-]+', '_', base, flags=re.UNICODE).strip('._') or 'photo'
    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return ''
    if not Path(base).stem:
        base = f'photo{ext}'
    return base[:200]


def _image_extension_ok(name):
    return Path(str(name or '')).suffix.lower() in ALLOWED_IMAGE_EXTS


def _zip_info_is_dir(info):
    raw = info.filename or ''
    if raw.endswith('\\') or raw.endswith('/'):
        return True
    return info.is_dir()


def persist_photo_zip(seller, payload: bytes, file_sha256: str) -> str:
    path = f'import_zips/{seller.pk}/{file_sha256}.zip'
    if not default_storage.exists(path):
        saved = default_storage.save(path, ContentFile(payload))
        return saved
    return path


def read_stored_zip(path: str) -> bytes:
    if not path:
        raise PhotoImportError('Архив предварительной проверки не найден.')
    if not default_storage.exists(path):
        raise PhotoImportError('Архив предварительной проверки не найден.')
    with default_storage.open(path, 'rb') as handle:
        return handle.read()


def _fieldfile_digest(fieldfile):
    if not fieldfile:
        return ''
    try:
        fieldfile.open('rb')
        try:
            data = fieldfile.read()
        finally:
            fieldfile.close()
    except (FileNotFoundError, OSError, ValueError):
        return ''
    if not data:
        return ''
    return hashlib.sha256(data).hexdigest()


def existing_photo_state(product):
    names = []
    digests = []
    if product.main_image:
        names.append(Path(product.main_image.name).name)
        digest = _fieldfile_digest(product.main_image)
        if digest:
            digests.append(digest)
    for item in product.images.all().order_by('sort_order', 'id'):
        names.append(Path(item.image.name).name)
        digest = _fieldfile_digest(item.image)
        if digest:
            digests.append(digest)
    return names, digests


def choose_primary(existing_main_digest, photos):
    ordered = sorted(
        photos,
        key=lambda item: (item.name.casefold(), item.name, item.digest),
    )
    if not ordered:
        return None
    if existing_main_digest:
        for photo in ordered:
            if photo.digest == existing_main_digest:
                return photo
    photoroom = [
        photo for photo in ordered
        if 'photoroom' in photo.name.casefold()
    ]
    if photoroom:
        return photoroom[0]
    return ordered[0]


def _match_product(seller, article):
    qs = Product.objects.filter(seller_profile=seller)
    exact = list(qs.filter(article=article)[:3])
    if len(exact) == 1:
        return exact[0], ''
    if len(exact) > 1:
        return None, 'Несколько товаров с этим артикулом у продавца.'
    insensitive = list(qs.filter(article__iexact=article)[:3])
    if len(insensitive) == 1:
        return insensitive[0], ''
    if len(insensitive) > 1:
        return None, 'Несколько товаров с этим артикулом у продавца.'
    return None, 'Товар с этим артикулом не найден у выбранного продавца.'


def _read_zip_images(payload: bytes):
    try:
        bundle = zipfile.ZipFile(BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise PhotoImportError('Не удалось прочитать ZIP.') from exc

    files = [info for info in bundle.infolist() if not _zip_info_is_dir(info)]
    if len(files) > MAX_FILE_COUNT:
        bundle.close()
        raise PhotoImportError(
            f'В архиве больше {MAX_FILE_COUNT} файлов. Разбейте архив на части.'
        )

    unsafe = []
    grouped = {}
    skipped_members = []
    uncompressed = 0
    with bundle:
        for info in files:
            parts, reason = _inspect_zip_member(info.filename)
            if parts is None:
                unsafe.append({'name': info.filename, 'error': reason})
                continue
            if any(_is_skipped_dir(part) for part in parts[:-1]):
                skipped_members.append(info.filename)
                continue
            filename = parts[-1]
            if _is_skipped_file(filename):
                skipped_members.append(info.filename)
                continue
            if len(parts) < 2:
                skipped_members.append(info.filename)
                continue
            folder = parts[-2]
            if not _image_extension_ok(filename):
                skipped_members.append(info.filename)
                continue
            safe_name = safe_image_filename(filename)
            if not safe_name:
                skipped_members.append(info.filename)
                continue
            with bundle.open(info) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise PhotoImportError(
                    f'Файл {info.filename} больше {MAX_FILE_BYTES // (1024 * 1024)} МБ.'
                )
            uncompressed += len(data)
            if uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise PhotoImportError(
                    'Распакованный размер архива превышает допустимый лимит.'
                )
            digest = hashlib.sha256(data).hexdigest()
            grouped.setdefault(folder, []).append(
                PhotoFile(
                    name=safe_name,
                    data=data,
                    digest=digest,
                    zip_name=info.filename,
                )
            )
    return grouped, unsafe, skipped_members


def _dedupe_photos(photos):
    unique = []
    seen = set()
    used_names = {}
    for photo in sorted(photos, key=lambda item: (item.name.casefold(), item.zip_name)):
        if photo.digest in seen:
            continue
        seen.add(photo.digest)
        name = photo.name
        stem = Path(name).stem
        ext = Path(name).suffix
        if name in used_names:
            used_names[name] += 1
            name = f'{stem}_{used_names[name]}{ext}'
        else:
            used_names[name] = 1
        unique.append(
            PhotoFile(
                name=name,
                data=photo.data,
                digest=photo.digest,
                zip_name=photo.zip_name,
            )
        )
    return unique


def plan_product_photo_import(seller, payload: bytes):
    grouped, unsafe, _skipped_members = _read_zip_images(payload)
    whitelist = seller_photo_whitelist(seller)
    rows = []

    for item in unsafe:
        label = {
            'path_traversal': 'Путь содержит .. и отклонён.',
            'absolute_path': 'Абсолютный путь в архиве отклонён.',
            'empty_path': 'Пустое имя файла в архиве.',
        }.get(item['error'], 'Небезопасный путь в архиве.')
        rows.append(
            PhotoImportRow(
                folder_name=item['name'],
                article='',
                action=CatalogImportItem.ACTION_ERROR,
                display_status='error',
                errors=[label],
            )
        )

    resolved_folders = {}
    for folder in grouped:
        resolved = resolve_photo_article(folder)
        resolved_folders.setdefault(resolved, []).append(folder)

    for folder, photos in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        photos = _dedupe_photos(photos)
        resolved = resolve_photo_article(folder)
        row = PhotoImportRow(
            folder_name=folder,
            article=resolved,
            action='',
            display_status='',
            alias_used=folder if resolved != folder else '',
            photos=photos,
        )
        if len(resolved_folders.get(resolved, [])) > 1:
            row.action = CatalogImportItem.ACTION_CONFLICT
            row.display_status = 'duplicate'
            row.errors.append('Несколько папок в ZIP соответствуют одному артикулу.')
            rows.append(row)
            continue
        if whitelist is not None and article_key(resolved) not in whitelist:
            row.action = CatalogImportItem.ACTION_SKIPPED
            row.display_status = 'skipped'
            row.errors.append('Папка не входит в разрешённый набор артикулов этого импорта.')
            rows.append(row)
            continue
        product, match_error = _match_product(seller, resolved)
        if product is None:
            row.action = CatalogImportItem.ACTION_SKIPPED
            row.display_status = 'missing'
            row.errors.append(match_error)
            rows.append(row)
            continue
        row.product = product
        names, digests = existing_photo_state(product)
        row.existing_names = names
        row.existing_hashes = digests
        incoming = [photo.digest for photo in photos]
        row.baseline = {
            'main_name': Path(product.main_image.name).name if product.main_image else '',
            'gallery_names': [
                Path(item.image.name).name
                for item in product.images.all().order_by('sort_order', 'id')
            ],
            'hashes': digests,
        }
        if sorted(incoming) == sorted(digests) and incoming:
            row.action = CatalogImportItem.ACTION_UNCHANGED
        else:
            row.action = CatalogImportItem.ACTION_UPDATED
        row.display_status = 'matched'
        rows.append(row)

    for row in rows:
        row.changed_fields = {
            'folder': row.folder_name,
            'alias': row.alias_used,
            'resolved_article': row.article,
            'status': row.display_status,
            'photo_count': row.photo_count,
            'photo_names': [photo.name for photo in row.photos],
            'photo_hashes': [photo.digest for photo in row.photos],
            'existing_names': row.existing_names,
            'existing_hashes': row.existing_hashes,
            'baseline': row.baseline,
        }
    return rows


def summarize_photo_rows(rows):
    summary = {
        'rows': len(rows),
        'matched': 0,
        'updated': 0,
        'unchanged': 0,
        'skipped': 0,
        'missing': 0,
        'duplicates': 0,
        'errors': 0,
        'photo_count': 0,
        'has_blockers': False,
    }
    for item in rows:
        summary['photo_count'] += item.photo_count
        if item.display_status == 'matched':
            summary['matched'] += 1
        if item.action == CatalogImportItem.ACTION_UPDATED:
            summary['updated'] += 1
        elif item.action == CatalogImportItem.ACTION_UNCHANGED:
            summary['unchanged'] += 1
        elif item.action == CatalogImportItem.ACTION_SKIPPED:
            summary['skipped'] += 1
            if item.display_status == 'missing':
                summary['missing'] += 1
        elif item.action == CatalogImportItem.ACTION_CONFLICT:
            summary['duplicates'] += 1
        elif item.action == CatalogImportItem.ACTION_ERROR:
            summary['errors'] += 1
    summary['has_blockers'] = bool(summary['duplicates'] or summary['errors'])
    return summary


def persist_photo_import_batch(
    *,
    seller,
    rows,
    filename,
    file_sha256,
    mode,
    source_archive_path='',
    uploaded_by=None,
    applied_by=None,
    status=None,
):
    summary = summarize_photo_rows(rows)
    now = timezone.now()
    if status is None:
        status = CatalogImportBatch.STATUS_SUCCESS
    batch = CatalogImportBatch.objects.create(
        seller_profile=seller,
        source=CatalogImportBatch.SOURCE_PRODUCT_PHOTOS,
        filename=filename,
        file_sha256=file_sha256,
        source_archive_path=source_archive_path or '',
        archive_status=(
            CatalogImportBatch.ARCHIVE_SUCCESS
            if source_archive_path
            else CatalogImportBatch.ARCHIVE_NOT_APPLICABLE
        ),
        started_at=now,
        finished_at=now,
        mode=mode,
        source_scope=CatalogImportBatch.SCOPE_PARTIAL,
        status=status,
        source_row_count=summary['rows'],
        source_unique_count=summary['matched'] + summary['duplicates'] + summary['errors'],
        selected_count=summary['matched'],
        created_count=0,
        updated_count=summary['updated'],
        unchanged_count=summary['unchanged'],
        skipped_count=summary['skipped'],
        conflict_count=summary['duplicates'],
        warning_count=0,
        error_count=summary['errors'],
        missing_from_source_count=summary['missing'],
        uploaded_by=uploaded_by,
        applied_by=applied_by,
    )
    item_rows = []
    for item in rows:
        item_rows.append(
            CatalogImportItem(
                batch=batch,
                product=item.product,
                article=item.article or item.folder_name or '',
                action=item.action,
                warnings=list(item.warnings or []),
                errors=list(item.errors or []),
                changed_fields=dict(item.changed_fields or {}),
            )
        )
    if item_rows:
        CatalogImportItem.objects.bulk_create(item_rows)
    return batch, summary


def preview_photo_display_rows(batch):
    rows = []
    for item in batch.items.select_related('product').all():
        fields = item.changed_fields or {}
        product = item.product
        rows.append({
            'article': item.article,
            'product_id': getattr(product, 'pk', None),
            'title': getattr(product, 'title', '') or '—',
            'photo_count': fields.get('photo_count') or 0,
            'status': fields.get('status') or item.action,
            'alias': fields.get('alias') or '',
            'existing_names': fields.get('existing_names') or [],
            'photo_names': fields.get('photo_names') or [],
            'folder': fields.get('folder') or '',
            'action': item.action,
            'errors': item.errors or [],
            'warnings': item.warnings or [],
        })
    return rows


def apply_product_photos(product, photos):
    if not photos:
        return
    existing_main = _fieldfile_digest(product.main_image)
    primary = choose_primary(existing_main, photos)
    if primary is None:
        return

    if existing_main != primary.digest:
        product.main_image.save(primary.name, ContentFile(primary.data), save=True)

    remaining = []
    seen = {primary.digest}
    for photo in sorted(photos, key=lambda item: (item.name.casefold(), item.name)):
        if photo.digest in seen:
            continue
        seen.add(photo.digest)
        remaining.append(photo)

    existing_gallery = {
        _fieldfile_digest(item.image): item
        for item in product.images.all()
        if _fieldfile_digest(item.image)
    }
    keep_ids = []
    for order, photo in enumerate(remaining, start=1):
        found = existing_gallery.get(photo.digest)
        if found is not None:
            fields = []
            if found.sort_order != order:
                found.sort_order = order
                fields.append('sort_order')
            if found.is_primary:
                found.is_primary = False
                fields.append('is_primary')
            if fields:
                found.save(update_fields=fields)
            keep_ids.append(found.pk)
        else:
            gallery = ProductImage(
                product=product,
                sort_order=order,
                is_primary=False,
            )
            gallery.image.save(photo.name, ContentFile(photo.data), save=True)
            keep_ids.append(gallery.pk)
    extras = product.images.exclude(pk__in=keep_ids)
    extras.delete()


def apply_photo_import_preview(*, seller, preview_batch, applied_by):
    if preview_batch.seller_profile_id != seller.pk:
        raise PhotoImportBlockedError('Пакет принадлежит другому продавцу.')
    if preview_batch.source != CatalogImportBatch.SOURCE_PRODUCT_PHOTOS:
        raise PhotoImportBlockedError('Это не пакет загрузки фотографий.')
    if preview_batch.mode != CatalogImportBatch.MODE_DRY_RUN:
        raise PhotoImportBlockedError('Применить можно только предварительную проверку.')
    items = list(preview_batch.items.select_related('product').all())
    if any(
        item.action in {
            CatalogImportItem.ACTION_CONFLICT,
            CatalogImportItem.ACTION_ERROR,
        }
        for item in items
    ):
        raise PhotoImportBlockedError(
            'В предварительной проверке есть конфликты или ошибки.'
        )

    payload = read_stored_zip(preview_batch.source_archive_path)
    planned = {
        (row.folder_name, row.article): row
        for row in plan_product_photo_import(seller, payload)
    }

    write_rows = []
    with transaction.atomic():
        product_ids = sorted({item.product_id for item in items if item.product_id})
        locked = {
            product.pk: product
            for product in Product.objects.select_for_update()
            .filter(pk__in=product_ids)
            .prefetch_related('images')
        }
        for item in items:
            fields = item.changed_fields or {}
            key = (fields.get('folder') or '', item.article)
            planned_row = planned.get(key)
            if planned_row is None and item.article:
                planned_row = next(
                    (
                        row for row in planned.values()
                        if row.article == item.article
                    ),
                    None,
                )
            if item.product_id is None:
                write_rows.append(
                    PhotoImportRow(
                        folder_name=fields.get('folder') or '',
                        article=item.article,
                        action=item.action,
                        display_status=fields.get('status') or item.action,
                        alias_used=fields.get('alias') or '',
                        errors=list(item.errors or []),
                        warnings=list(item.warnings or []),
                        changed_fields=dict(fields),
                    )
                )
                continue
            product = locked.get(item.product_id)
            if product is None:
                raise PhotoImportStaleError(STALE_APPLY_MESSAGE)
            _names, current_hashes = existing_photo_state(product)
            expected = (fields.get('baseline') or {}).get('hashes') or []
            if sorted(current_hashes) != sorted(expected):
                raise PhotoImportStaleError(STALE_APPLY_MESSAGE)
            if planned_row is None:
                raise PhotoImportStaleError(STALE_APPLY_MESSAGE)
            if item.action in {
                CatalogImportItem.ACTION_UPDATED,
                CatalogImportItem.ACTION_UNCHANGED,
            }:
                apply_product_photos(product, planned_row.photos)
            write_item = PhotoImportRow(
                folder_name=fields.get('folder') or '',
                article=item.article,
                action=item.action,
                display_status=fields.get('status') or 'matched',
                product=product,
                alias_used=fields.get('alias') or '',
                errors=list(item.errors or []),
                warnings=list(item.warnings or []),
                photos=list(planned_row.photos if planned_row else []),
                existing_names=fields.get('existing_names') or [],
                changed_fields=dict(fields),
            )
            write_rows.append(write_item)
        write_batch, summary = persist_photo_import_batch(
            seller=seller,
            rows=write_rows,
            filename=preview_batch.filename,
            file_sha256=preview_batch.file_sha256,
            mode=CatalogImportBatch.MODE_WRITE,
            source_archive_path=preview_batch.source_archive_path,
            uploaded_by=preview_batch.uploaded_by,
            applied_by=applied_by,
            status=CatalogImportBatch.STATUS_SUCCESS,
        )
    return write_batch, summary
