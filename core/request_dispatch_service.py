from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from core.models import BroadcastSettings, Match, RequestDispatch, WhatsAppMessageLog

logger = logging.getLogger(__name__)

WAVE_INTERVAL_FALLBACK_MINUTES = 5
MAX_DISPATCH_SEND_ATTEMPTS = 3
BUYER_WHATSAPP_LOG_SELLER_NAME = 'Покупатель'
SQLITE_WAVE_LOCKS: dict[tuple[int, int], threading.Lock] = {}
SQLITE_WAVE_LOCKS_GUARD = threading.Lock()


def build_successful_whatsapp_log_index(request_id: int) -> set[tuple[str, str]]:
    return {
        (seller_name, phone_clean)
        for seller_name, phone_clean in WhatsAppMessageLog.objects.filter(
            request_id=request_id,
            is_success=True,
        )
        .exclude(seller_name=BUYER_WHATSAPP_LOG_SELLER_NAME)
        .values_list('seller_name', 'phone_clean')
    }


def has_successful_whatsapp_log(
    dispatch: RequestDispatch,
    *,
    success_log_keys: set[tuple[str, str]] | None = None,
) -> bool:
    key = (dispatch.seller.name, _normalize_phone(dispatch.seller.whatsapp))
    if success_log_keys is not None:
        return key in success_log_keys
    return WhatsAppMessageLog.objects.filter(
        request_id=dispatch.request_id,
        phone_clean=key[1],
        seller_name=key[0],
        is_success=True,
    ).exclude(
        seller_name=BUYER_WHATSAPP_LOG_SELLER_NAME,
    ).exists()


def resolve_whatsapp_status(
    dispatch: RequestDispatch,
    match: Match | None = None,
    *,
    success_log_keys: set[tuple[str, str]] | None = None,
) -> str:
    """UI status: sent / pending / error."""
    if dispatch.status == RequestDispatch.STATUS_SENT:
        return 'sent'
    if match is not None and match.status == 'sent':
        return 'sent'
    if has_successful_whatsapp_log(dispatch, success_log_keys=success_log_keys):
        return 'sent'
    if dispatch.status == RequestDispatch.STATUS_FAILED:
        return 'error'
    if match is not None and match.status == 'error':
        return 'error'
    return 'pending'


def broadcast_settings_block_reason(settings: BroadcastSettings) -> str | None:
    if settings.emergency_stop:
        return 'emergency_stop'
    if settings.mode == BroadcastSettings.MODE_OFF:
        return 'mode_off'
    return None


def seller_allowed_for_dispatch(seller, settings: BroadcastSettings) -> bool:
    if settings.mode == BroadcastSettings.MODE_TEST:
        return bool(seller.is_test_seller)
    if settings.mode == BroadcastSettings.MODE_LIVE:
        return True
    return False


def _wave_interval(settings: BroadcastSettings) -> timedelta:
    minutes = settings.wave_interval_minutes or WAVE_INTERVAL_FALLBACK_MINUTES
    return timedelta(minutes=minutes)


def _normalize_phone(phone: str) -> str:
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def dispatch_failure_count(dispatch: RequestDispatch) -> int:
    return WhatsAppMessageLog.objects.filter(
        request_id=dispatch.request_id,
        phone_clean=_normalize_phone(dispatch.seller.whatsapp),
        seller_name=dispatch.seller.name,
        is_success=False,
    ).exclude(
        seller_name=BUYER_WHATSAPP_LOG_SELLER_NAME,
    ).count()


def record_failed_send_attempt(
    dispatch: RequestDispatch,
    *,
    status_text: str = 'dispatch_error',
    error_text: str = '',
) -> None:
    """Persist a failed send attempt when Meta call did not write WhatsAppMessageLog."""
    WhatsAppMessageLog.objects.create(
        request_id=dispatch.request_id,
        seller_name=dispatch.seller.name,
        phone_clean=_normalize_phone(dispatch.seller.whatsapp) or '-',
        is_success=False,
        status_text=status_text,
        message_id='',
        error_text=error_text,
    )


def _previous_wave_allows_next(
    request_id: int,
    wave_number: int,
    *,
    settings: BroadcastSettings,
    now,
) -> bool:
    previous_wave = wave_number - 1
    if previous_wave < 1:
        return True

    if RequestDispatch.objects.filter(
        request_id=request_id,
        wave_number=previous_wave,
        status=RequestDispatch.STATUS_QUEUED,
    ).exists():
        return False

    previous_sent_at = (
        RequestDispatch.objects.filter(
            request_id=request_id,
            wave_number=previous_wave,
            status=RequestDispatch.STATUS_SENT,
            sent_at__isnull=False,
        ).aggregate(max_sent_at=Max('sent_at'))['max_sent_at']
    )
    if previous_sent_at is None:
        return True

    return now >= previous_sent_at + _wave_interval(settings)


def get_next_sendable_wave(
    request_id: int,
    *,
    settings: BroadcastSettings,
    now=None,
) -> int | None:
    """Return the lowest queued wave allowed to send now, or None."""
    now = now or timezone.now()

    queued_waves = list(
        RequestDispatch.objects.filter(
            request_id=request_id,
            status=RequestDispatch.STATUS_QUEUED,
        )
        .values_list('wave_number', flat=True)
        .distinct()
        .order_by('wave_number'),
    )
    if not queued_waves:
        return None

    next_wave = min(queued_waves)

    if RequestDispatch.objects.filter(
        request_id=request_id,
        status=RequestDispatch.STATUS_QUEUED,
        wave_number__lt=next_wave,
    ).exists():
        return None

    if not _previous_wave_allows_next(
        request_id,
        next_wave,
        settings=settings,
        now=now,
    ):
        return None

    return next_wave


def _is_postgresql() -> bool:
    return connection.vendor == 'postgresql'


def _sqlite_wave_lock(request_id: int, wave_number: int) -> threading.Lock:
    lock_key = (request_id, wave_number)
    with SQLITE_WAVE_LOCKS_GUARD:
        lock = SQLITE_WAVE_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            SQLITE_WAVE_LOCKS[lock_key] = lock
        return lock


def try_acquire_wave_lock(request_id: int, wave_number: int) -> bool:
    if _is_postgresql():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_try_advisory_lock(%s, %s)',
                [request_id, wave_number],
            )
            row = cursor.fetchone()
            return bool(row and row[0])

    return _sqlite_wave_lock(request_id, wave_number).acquire(blocking=False)


def release_wave_lock(request_id: int, wave_number: int) -> None:
    if _is_postgresql():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_advisory_unlock(%s, %s)',
                [request_id, wave_number],
            )
        return

    lock = _sqlite_wave_lock(request_id, wave_number)
    if lock.locked():
        lock.release()


class WaveRunLock:
    """PostgreSQL session / SQLite thread lock for one request wave."""

    def __init__(self, request_id: int, wave_number: int) -> None:
        self.request_id = request_id
        self.wave_number = wave_number
        self._acquired = False
        self._sqlite_lock: threading.Lock | None = None

    def __enter__(self) -> WaveRunLock:
        if _is_postgresql():
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT pg_try_advisory_lock(%s, %s)',
                    [self.request_id, self.wave_number],
                )
                row = cursor.fetchone()
                self._acquired = bool(row and row[0])
        else:
            self._sqlite_lock = _sqlite_wave_lock(self.request_id, self.wave_number)
            self._acquired = self._sqlite_lock.acquire(blocking=False)
        return self

    @property
    def acquired(self) -> bool:
        return self._acquired

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._acquired:
            return
        try:
            if _is_postgresql():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT pg_advisory_unlock(%s, %s)',
                        [self.request_id, self.wave_number],
                    )
            elif self._sqlite_lock is not None and self._sqlite_lock.locked():
                self._sqlite_lock.release()
        finally:
            self._acquired = False
            self._sqlite_lock = None


def record_dispatch_failure(dispatch: RequestDispatch, match: Match) -> None:
    match.status = 'error'
    match.save(update_fields=['status'])

    if dispatch_failure_count(dispatch) >= MAX_DISPATCH_SEND_ATTEMPTS:
        dispatch.status = RequestDispatch.STATUS_FAILED
        dispatch.save(update_fields=['status'])


def _mark_dispatch_sent(
    dispatch: RequestDispatch,
    match: Match,
    sent_at=None,
) -> None:
    """Idempotently mark Match and RequestDispatch as sent after Meta accepted the message."""
    sent_at = sent_at or timezone.now()

    match_update_fields = ['status']
    if match.status != 'sent':
        match.status = 'sent'
    if match.sent_at is None:
        match.sent_at = sent_at
        match_update_fields.append('sent_at')

    dispatch_update_fields = ['status']
    if dispatch.status != RequestDispatch.STATUS_SENT:
        dispatch.status = RequestDispatch.STATUS_SENT
    if dispatch.sent_at is None:
        dispatch.sent_at = sent_at
        dispatch_update_fields.append('sent_at')

    match.save(update_fields=match_update_fields)
    dispatch.save(update_fields=dispatch_update_fields)


def send_single_dispatch(dispatch: RequestDispatch) -> dict:
    """Send one dispatch via Meta API. sent_at is set only after success."""
    from core.views import send_whatsapp_template

    with transaction.atomic():
        locked_dispatch = (
            RequestDispatch.objects.select_for_update()
            .filter(
                pk=dispatch.pk,
                status=RequestDispatch.STATUS_QUEUED,
            )
            .select_related('request', 'seller')
            .first()
        )
        if locked_dispatch is None:
            return {'ok': False, 'skipped': True, 'reason': 'not_queued'}

        match, _ = Match.objects.get_or_create(
            request=locked_dispatch.request,
            seller=locked_dispatch.seller,
            defaults={'status': 'prepared'},
        )
        dispatch = locked_dispatch

    try:
        wa_result = send_whatsapp_template(
            dispatch.seller.whatsapp,
            dispatch.request,
            dispatch.seller.name,
        )
    except Exception as exc:
        with transaction.atomic():
            dispatch = RequestDispatch.objects.select_for_update().get(pk=dispatch.pk)
            if dispatch.status != RequestDispatch.STATUS_QUEUED:
                return {'ok': False, 'skipped': True, 'reason': 'status_changed'}
            match = Match.objects.get(request=dispatch.request, seller=dispatch.seller)
            record_failed_send_attempt(
                dispatch,
                status_text='exception',
                error_text=str(exc),
            )
            record_dispatch_failure(dispatch, match)
        logger.exception(
            'WhatsApp dispatch failed for request #%s seller #%s',
            dispatch.request_id,
            dispatch.seller_id,
        )
        return {'ok': False, 'error': str(exc)}

    if wa_result.get('ok'):
        sent_at = timezone.now()
        with transaction.atomic():
            dispatch = RequestDispatch.objects.select_for_update().get(pk=dispatch.pk)
            match, _ = Match.objects.get_or_create(
                request=dispatch.request,
                seller=dispatch.seller,
                defaults={'status': 'prepared'},
            )
            _mark_dispatch_sent(dispatch, match, sent_at)
        return {'ok': True, 'sent_at': sent_at}

    with transaction.atomic():
        dispatch = RequestDispatch.objects.select_for_update().get(pk=dispatch.pk)
        if dispatch.status != RequestDispatch.STATUS_QUEUED:
            return {'ok': False, 'skipped': True, 'reason': 'status_changed'}
        match = Match.objects.get(request=dispatch.request, seller=dispatch.seller)
        record_dispatch_failure(dispatch, match)
    return {'ok': False, 'error': wa_result.get('error')}


def process_due_dispatch_waves(*, writer=None) -> dict:
    """Process at most one eligible wave per request. Used by management command."""
    settings = BroadcastSettings.load()
    now = timezone.now()

    def write(message: str, style: str | None = None) -> None:
        if writer is None:
            logger.info(message)
            return
        if style == 'success':
            writer(message, style='SUCCESS')
        elif style == 'error':
            writer(message, style='ERROR')
        elif style == 'warning':
            writer(message, style='WARNING')
        else:
            writer(message)

    block_reason = broadcast_settings_block_reason(settings)
    write(
        'Broadcast settings: '
        f'mode={settings.mode}, wave_size={settings.wave_size}, '
        f'interval={settings.wave_interval_minutes} min, '
        f'emergency_stop={settings.emergency_stop}',
    )
    if block_reason:
        write(f'Dispatch waves skipped: {block_reason}', style='warning')
        return {
            'blocked': block_reason,
            'sent': 0,
            'errors': 0,
            'requests_processed': 0,
            'skipped_locks': 0,
        }

    request_ids = list(
        RequestDispatch.objects.filter(status=RequestDispatch.STATUS_QUEUED)
        .values_list('request_id', flat=True)
        .distinct()
        .order_by('request_id'),
    )

    total_sent = 0
    total_errors = 0
    requests_processed = 0
    skipped_locks = 0

    for request_id in request_ids:
        wave_number = get_next_sendable_wave(
            request_id,
            settings=settings,
            now=now,
        )
        if wave_number is None:
            continue

        with WaveRunLock(request_id, wave_number) as wave_lock:
            if not wave_lock.acquired:
                skipped_locks += 1
                write(
                    f'Request #{request_id} wave {wave_number}: '
                    'skipped because another worker holds the lock',
                )
                continue

            dispatches = list(
                RequestDispatch.objects.filter(
                    request_id=request_id,
                    wave_number=wave_number,
                    status=RequestDispatch.STATUS_QUEUED,
                )
                .select_related('request', 'seller')
                .order_by('position_number'),
            )
            if not dispatches:
                continue

            requests_processed += 1
            wave_sent = 0
            wave_errors = 0

            write(
                f'Request #{request_id}: processing wave {wave_number} '
                f'({len(dispatches)} seller(s))',
            )

            for dispatch in dispatches:
                if not seller_allowed_for_dispatch(dispatch.seller, settings):
                    write(
                        f'Request #{request_id} wave {wave_number}: '
                        f'skip seller #{dispatch.seller_id} ({settings.mode} mode)',
                    )
                    continue

                result = send_single_dispatch(dispatch)
                if result.get('skipped'):
                    continue
                if result.get('ok'):
                    wave_sent += 1
                    total_sent += 1
                    write(
                        f'SENT request #{request_id} wave {wave_number}: '
                        f'{dispatch.seller.name}',
                        style='success',
                    )
                else:
                    wave_errors += 1
                    total_errors += 1
                    write(
                        f'ERROR request #{request_id} wave {wave_number}: '
                        f'{dispatch.seller.name}',
                        style='error',
                    )

            write(
                f'Request #{request_id} wave {wave_number} finished: '
                f'sent={wave_sent}, errors={wave_errors}',
            )

    write(
        f'Finished dispatch waves: requests={requests_processed}, '
        f'sent={total_sent}, errors={total_errors}, skipped_locks={skipped_locks}',
        style='success',
    )
    return {
        'blocked': None,
        'sent': total_sent,
        'errors': total_errors,
        'requests_processed': requests_processed,
        'skipped_locks': skipped_locks,
    }
