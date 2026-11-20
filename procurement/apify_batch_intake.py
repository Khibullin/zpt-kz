"""Durable Apify JSON intake and 500-source-row checkpoints.

Original rows are staged temporarily until processed; completed source batches
are deleted so rejected low-margin products are not retained in the system.
A failed batch remains available to retry idempotently.
This is a browser-driven sequential worker; no unattended background promise.
"""
import hashlib
import json

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from .apify_buying_import import _normalize_rows, import_apify_results
from .models import Activity

BATCH_SIZE = 500
MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 50000


def stage_apify_file(upload, user):
    if not upload.name.lower().endswith('.json') or upload.size > MAX_BYTES:
        raise ValidationError('Нужен JSON Apify размером не более 50 МБ.')
    data = upload.read()
    digest = hashlib.sha256(data).hexdigest()
    try:
        raw = json.loads(data.decode('utf-8-sig'))
        rows = _normalize_rows(raw)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ValidationError('Не удалось прочитать JSON Apify.') from exc
    if not rows or len(rows) > MAX_ROWS:
        raise ValidationError('Допустимо от 1 до 50 000 исходных записей.')
    # Exact original JSON values are retained; never drop rejected input rows.
    with transaction.atomic():
        previous = Activity.objects.select_for_update().filter(
            action='apify_upload_session', details__digest=digest, user=user
        ).order_by('-id').first()
        if previous and previous.details.get('next_batch', 0) < previous.details.get('total_batches', 0):
            return previous
        # A completed upload may be deliberately reprocessed after a matching
        # policy fix. Keep the prior session and its audit trail intact.
        total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
        session = Activity.objects.create(user=user, action='apify_upload_session',
            details={'digest': digest, 'filename': upload.name[:255],
                     'total': len(rows), 'total_batches': total_batches,
                     'next_batch': 0, 'stats': {'products': 0, 'linked': 0,
                     'verified': 0, 'unmatched': 0, 'invalid': 0,
                     'protected_verified': 0, 'rejected_below_50': 0,
                     'rejected_unverifiable': 0, 'rejected_unconfirmed_price': 0,
                     'recheck_matched_cards': 0, 'recheck_price_cards': 0,
                     'recheck_unrelated': 0, 'recheck_missing_sellers': 0,
                     'recheck_offer_updates': 0}})
        Activity.objects.bulk_create([
            Activity(user=user, action='apify_upload_source_batch',
                     details={'session_id': session.pk, 'batch': i,
                              'rows': rows[i * BATCH_SIZE:(i + 1) * BATCH_SIZE]})
            for i in range(total_batches)
        ], batch_size=25)
        return session


def process_next_apify_batch(session_id, user):
    with transaction.atomic():
        session = Activity.objects.select_for_update().get(
            pk=session_id, user=user, action='apify_upload_session')
        details = dict(session.details)
        next_batch = details['next_batch']
        if details.get('next_upload_batch', details['total_batches']) < details['total_batches']:
            raise ValidationError('Сначала завершите загрузку всех исходных партий.')
        if next_batch >= details['total_batches']:
            return {'done': True, **details}
        source = Activity.objects.get(
            action='apify_upload_source_batch',
            details__session_id=session.pk, details__batch=next_batch)
        raw = source.details['rows']
        data = json.dumps(raw, ensure_ascii=False).encode('utf-8')
        uploaded = SimpleUploadedFile(details['filename'], data,
                                     content_type='application/json')
        result = import_apify_results(uploaded, details['filename'], user=user)
        stats = dict(details['stats'])
        for key in stats:
            stats[key] += result.get(key, 0)
        details['stats'] = stats
        details['next_batch'] += 1
        session.details = details
        session.save(update_fields=['details'])
        # Source rows were needed only for reliable retry. Do not keep
        # rejected or below-threshold cards in permanent storage.
        source.delete()
        return {'done': details['next_batch'] >= details['total_batches'], **details}


def begin_chunked_upload(*, filename, total, digest, user):
    """Create/resume a source file using tiny, independent browser requests."""
    if not str(filename).lower().endswith('.json'):
        raise ValidationError('Требуется файл JSON.')
    if not isinstance(total, int) or not 1 <= total <= MAX_ROWS:
        raise ValidationError('Допустимо от 1 до 50 000 исходных строк.')
    if not isinstance(digest, str) or len(digest) != 64 or any(
            c not in '0123456789abcdef' for c in digest):
        raise ValidationError('Некорректный отпечаток исходного файла.')
    with transaction.atomic():
        previous = Activity.objects.select_for_update().filter(
            user=user, action='apify_upload_session', details__digest=digest
        ).order_by('-id').first()
        if previous:
            if previous.details.get('total') != total:
                raise ValidationError('Число строк отличается от ранее сохранённого файла.')
            if previous.details.get('next_batch', 0) < previous.details.get('total_batches', 0):
                return previous
            # Identical completed JSON can be explicitly uploaded again after
            # an importer bug fix. This is a new, auditable processing session.
        return Activity.objects.create(
            user=user, action='apify_upload_session',
            details={'digest': digest, 'filename': filename[:255],
                     'total': total, 'total_batches': (total + BATCH_SIZE - 1) // BATCH_SIZE,
                     'next_batch': 0, 'next_upload_batch': 0,
                     'stats': {'products': 0, 'linked': 0, 'verified': 0,
                               'unmatched': 0, 'invalid': 0,
                               'protected_verified': 0, 'rejected_below_50': 0,
                               'rejected_unverifiable': 0, 'rejected_unconfirmed_price': 0,
                     'recheck_matched_cards': 0, 'recheck_price_cards': 0,
                     'recheck_unrelated': 0, 'recheck_missing_sellers': 0,
                     'recheck_offer_updates': 0}})


def append_chunked_upload(*, session_id, batch, rows, user):
    """Persist one original batch atomically, supporting safe retry."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= BATCH_SIZE:
        raise ValidationError('Одна партия должна содержать от 1 до 500 строк.')
    with transaction.atomic():
        session = Activity.objects.select_for_update().get(
            pk=session_id, user=user, action='apify_upload_session')
        details = dict(session.details)
        expected = details.get('next_upload_batch', details['total_batches'])
        if batch < expected:
            return {'uploaded_batches': expected, 'total_batches': details['total_batches']}
        if batch != expected or batch >= details['total_batches']:
            raise ValidationError('Неверный порядок загрузки партий.')
        required_size = min(BATCH_SIZE, details['total'] - batch * BATCH_SIZE)
        if len(rows) != required_size:
            raise ValidationError('Неверное количество строк в партии.')
        Activity.objects.create(
            user=user, action='apify_upload_source_batch',
            details={'session_id': session.pk, 'batch': batch, 'rows': rows})
        details['next_upload_batch'] = expected + 1
        session.details = details
        session.save(update_fields=['details'])
        return {'uploaded_batches': details['next_upload_batch'],
                'total_batches': details['total_batches']}
