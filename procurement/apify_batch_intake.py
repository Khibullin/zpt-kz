"""Durable Apify JSON intake and 500-source-row checkpoints.

Original rows, including rejected cards, are retained for policy replay.
Completed batches are compressed in the existing private activity archive.
A failed batch remains available to retry idempotently.
This is a browser-driven sequential worker; no unattended background promise.
"""
import base64
import gzip
import hashlib
import json

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from .apify_buying_import import _normalize_rows, import_apify_results
from .models import Activity

BATCH_SIZE = 500
MAX_BYTES = 200 * 1024 * 1024
MAX_ROWS = 50000


def stage_apify_file(upload, user, *, request_id=None):
    if not upload.name.lower().endswith('.json') or upload.size > MAX_BYTES:
        raise ValidationError('Нужен JSON Apify размером не более 200 МБ.')
    data = upload.read()
    digest = hashlib.sha256(data).hexdigest()
    try:
        raw = json.loads(data.decode('utf-8-sig'))
        rows = _normalize_rows(raw)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ValidationError('Не удалось прочитать JSON Apify.') from exc
    if not rows or len(rows) > MAX_ROWS:
        raise ValidationError('Допустимо от 1 до 50 000 исходных записей.')
    from .request_snapshot_import import intake_context, audit_source
    scope = intake_context(request_id)
    # Exact original JSON values are retained; never drop rejected input rows.
    with transaction.atomic():
        previous = Activity.objects.select_for_update().filter(
            action='apify_upload_session', details__digest=digest, user=user
        ).order_by('-id').first()
        if previous and previous.details.get('selection_request_id') != scope.get('selection_request_id'):
            raise ValidationError('Этот JSON уже загружен для другого запроса. Для нового цикла нужны новые результаты Apify.')
        if previous and previous.details.get('next_batch', 0) < previous.details.get('total_batches', 0):
            return previous
        # A completed upload may be deliberately reprocessed after a matching
        # policy fix. Keep the prior session and its audit trail intact.
        total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
        session = Activity.objects.create(user=user, action='apify_upload_session',
            details={**scope, 'audit_source': audit_source(upload.name, request_id),
                     'digest': digest, 'filename': upload.name[:255],
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
        raw = source_rows(source)
        data = json.dumps(raw, ensure_ascii=False).encode('utf-8')
        uploaded = SimpleUploadedFile(details['filename'], data,
                                     content_type='application/json')
        if details.get('selection_request_id'):
            from .request_snapshot_import import selected_request
            from .apify_price_recheck import import_apify_recheck_results
            result = import_apify_recheck_results(raw, details['audit_source'], user,
                selection_request=selected_request(details['selection_request_id']),
                evidence_filename=details['filename'])
        else:
            result = import_apify_results(uploaded, details['filename'], user=user)
        stats = dict(details['stats'])
        for key in stats:
            stats[key] += result.get(key, 0)
        details['stats'] = stats
        details['next_batch'] += 1
        details['last_progress_at'] = timezone.now().isoformat()
        details.pop('last_error', None)
        session.details = details
        session.save(update_fields=['details'])
        # Keep every original JSON value, compressing only its representation.
        source.details = {'session_id': session.pk, 'batch': next_batch,
                          'rows_gzip': base64.b64encode(gzip.compress(data, mtime=0)).decode('ascii')}
        source.save(update_fields=['details'])
        return {'done': details['next_batch'] >= details['total_batches'], **details}


def begin_chunked_upload(*, filename, total, digest, user, request_id=None):
    """Create/resume a source file using tiny, independent browser requests."""
    if not str(filename).lower().endswith('.json'):
        raise ValidationError('Требуется файл JSON.')
    if not isinstance(total, int) or not 1 <= total <= MAX_ROWS:
        raise ValidationError('Допустимо от 1 до 50 000 исходных строк.')
    if not isinstance(digest, str) or len(digest) != 64 or any(
            c not in '0123456789abcdef' for c in digest):
        raise ValidationError('Некорректный отпечаток исходного файла.')
    from .request_snapshot_import import intake_context, audit_source
    scope = intake_context(request_id)
    with transaction.atomic():
        previous = Activity.objects.select_for_update().filter(
            user=user, action='apify_upload_session', details__digest=digest
        ).order_by('-id').first()
        if previous:
            if previous.details.get('selection_request_id') != scope.get('selection_request_id'):
                raise ValidationError('Этот JSON уже загружен для другого запроса. Для нового цикла нужны новые результаты Apify.')
            if previous.details.get('total') != total:
                raise ValidationError('Число строк отличается от ранее сохранённого файла.')
            if previous.details.get('next_batch', 0) < previous.details.get('total_batches', 0):
                return previous
            # Identical completed JSON can be explicitly uploaded again after
            # an importer bug fix. This is a new, auditable processing session.
        return Activity.objects.create(
            user=user, action='apify_upload_session',
            details={**scope, 'audit_source': audit_source(filename, request_id),
                     'digest': digest, 'filename': filename[:255],
                     'total': total, 'total_batches': (total + BATCH_SIZE - 1) // BATCH_SIZE,
                     'next_batch': 0, 'next_upload_batch': 0,
                     'last_progress_at': timezone.now().isoformat(),
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
        details['last_progress_at'] = timezone.now().isoformat()
        details.pop('last_error', None)
        session.details = details
        session.save(update_fields=['details'])
        return {'uploaded_batches': details['next_upload_batch'],
                'total_batches': details['total_batches']}


def upload_status(source=''):
    """Read-only progress for a specific source filename or latest session.

    Processing is driven by the upload browser tab; an abandoned session does
    not imply a server worker is still running. Do not call it an error unless
    the importer actually recorded an error.
    """
    sessions = Activity.objects.filter(action='apify_upload_session')
    if source:
        sessions = sessions.filter(details__filename=source)
    session = sessions.order_by('-id').first()
    if session is None:
        return {'state': 'unknown', 'label': 'Статус неизвестен',
                'message': 'Для выбранной выгрузки нет сохранённой сессии обработки.'}
    details = session.details or {}
    total_batches = max(int(details.get('total_batches') or 0), 0)
    completed = min(max(int(details.get('next_batch') or 0), 0), total_batches)
    uploaded = min(max(int(details.get('next_upload_batch', total_batches) or 0), 0), total_batches)
    total_cards = max(int(details.get('total') or 0), 0)
    processed_cards = min(completed * BATCH_SIZE, total_cards)
    last_progress = details.get('last_progress_at')
    try:
        recent = timezone.datetime.fromisoformat(last_progress) if last_progress else session.created_at
        if timezone.is_naive(recent):
            recent = timezone.make_aware(recent)
    except (ValueError, TypeError):
        recent = session.created_at
    if total_batches > 0 and completed >= total_batches and uploaded >= total_batches:
        state, label = 'complete', 'Завершено'
    elif details.get('last_error'):
        state, label = 'error', 'Ошибка обработки'
    elif (timezone.now() - recent).total_seconds() > 15 * 60:
        state, label = 'paused', 'Ожидает продолжения'
    else:
        state, label = 'running', 'Идёт обработка' if uploaded >= total_batches else 'Идёт загрузка'
    message = (str(details.get('last_error') or '')[:180] if state == 'error' else
               'Откройте страницу подготовки и продолжите обработку.' if state == 'paused' else '')
    return {'state': state, 'label': label, 'message': message,
            'filename': str(details.get('filename', '')),
            'total_batches': total_batches, 'completed_batches': completed,
            'uploaded_batches': uploaded, 'total_cards': total_cards,
            'processed_cards': processed_cards, 'errors': int((details.get('stats') or {}).get('invalid') or 0)}


def source_rows(source):
    details = source.details
    if 'rows' in details:
        return details['rows']
    return json.loads(gzip.decompress(base64.b64decode(details['rows_gzip'])))


@transaction.atomic
def replay_apify_session(session_id, user):
    """Stage a new processing session from retained rows; never call Apify."""
    original = Activity.objects.select_for_update().get(
        pk=session_id, user=user, action='apify_upload_session')
    details = dict(original.details)
    if details.get('next_upload_batch', details['total_batches']) != details['total_batches']:
        raise ValidationError('Исходная загрузка ещё не завершена.')
    sources = list(Activity.objects.filter(user=user, action='apify_upload_source_batch',
        details__session_id=original.pk).order_by('id'))
    if len(sources) != details['total_batches']:
        raise ValidationError('Исходные строки старой сессии не сохранены. Нужен исходный JSON.')
    details.update(next_batch=0, next_upload_batch=details['total_batches'],
                   replay_of=original.pk, last_progress_at=timezone.now().isoformat(),
                   stats={key: 0 for key in details['stats']})
    details.pop('last_error', None)
    details.pop('buying_import_result', None)
    replay = Activity.objects.create(user=user, action='apify_upload_session', details=details)
    for source in sources:
        copied = dict(source.details)
        copied['session_id'] = replay.pk
        Activity.objects.create(user=user, action='apify_upload_source_batch', details=copied)
    return replay
