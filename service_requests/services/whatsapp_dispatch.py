from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.whatsapp_redaction import (
    redact_whatsapp_sensitive_data,
    sanitize_persisted_whatsapp_error_message,
)
from core.whatsapp_template_sender import (
    normalize_whatsapp_phone,
    send_whatsapp_template_message,
    wa_template_param,
)
from service_requests.models import (
    ServiceBroadcastSettings,
    ServiceMatch,
    ServiceRequestDispatch,
    ServiceWhatsAppMessageLog,
)
from service_requests.services.dispatch_policy import get_service_broadcast_mode

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (60, 300)
STALE_PROCESSING_AFTER = timedelta(minutes=5)
BATCH_SIZE = 100

SKIP_BROADCAST_DISABLED = 'broadcast_disabled'
SKIP_NOT_TEST_SELLER = 'not_test_seller'
SKIP_SELLER_INACTIVE = 'seller_inactive'
SKIP_RECEIVE_REQUESTS_DISABLED = 'receive_requests_disabled'
SKIP_SELLER_PAUSED = 'seller_paused'

DEFAULT_TEMPLATE_NAME = 'zpt_request_notification'
DEFAULT_TEMPLATE_LANGUAGE = 'ru'


def enqueue_service_request_dispatches(req, sellers):
    """Create one match and one queued dispatch per selected seller.

    Repeated calls keep the existing row for the same request and seller.
    """
    matched = []
    for position, seller in enumerate(sellers, start=1):
        ServiceMatch.objects.get_or_create(
            request=req,
            seller=seller,
        )
        ServiceRequestDispatch.objects.get_or_create(
            request=req,
            seller=seller,
            defaults={
                'position_number': position,
                'status': ServiceRequestDispatch.STATUS_QUEUED,
            },
        )
        matched.append(seller)
    return matched


def service_request_template_call(req, seller) -> dict:
    services_text = ', '.join(
        req.services.order_by('pk').values_list('name', flat=True)
    ) or '-'
    return {
        'to_phone': seller.whatsapp,
        'template_name': os.getenv(
            'WHATSAPP_SERVICE_TEMPLATE_NAME',
            DEFAULT_TEMPLATE_NAME,
        ),
        'template_language': os.getenv(
            'WHATSAPP_TEMPLATE_LANG',
            DEFAULT_TEMPLATE_LANGUAGE,
        ),
        'body_parameters': [
            wa_template_param(req.id),
            wa_template_param(req.brand),
            wa_template_param(req.model),
            wa_template_param(services_text),
            wa_template_param(req.city),
            wa_template_param(req.description),
            wa_template_param(req.phone),
        ],
    }


def process_due_service_request_dispatches(*, writer=None, batch_size=BATCH_SIZE) -> dict:
    now = timezone.now()
    recovered = _recover_stale_processing(now)
    due_ids = list(
        ServiceRequestDispatch.objects.filter(
            status=ServiceRequestDispatch.STATUS_QUEUED,
            next_attempt_at__lte=now,
        )
        .order_by('next_attempt_at', 'request_id', 'position_number', 'pk')
        .values_list('pk', flat=True)[:batch_size]
    )

    counts = {
        'recovered': recovered,
        'sent': 0,
        'retried': 0,
        'failed': 0,
        'skipped': 0,
        'reconciled': 0,
    }
    for dispatch_id in due_ids:
        outcome = _process_dispatch(dispatch_id)
        if outcome in counts:
            counts[outcome] += 1

    message = (
        'Service request dispatches: '
        f"recovered={counts['recovered']} sent={counts['sent']} "
        f"retried={counts['retried']} failed={counts['failed']} "
        f"skipped={counts['skipped']} reconciled={counts['reconciled']}"
    )
    if writer is None:
        logger.info(message)
    else:
        writer(message)
    return counts


def _recover_stale_processing(now) -> int:
    cutoff = now - STALE_PROCESSING_AFTER
    return ServiceRequestDispatch.objects.filter(
        status=ServiceRequestDispatch.STATUS_PROCESSING,
        last_attempt_at__lt=cutoff,
    ).update(
        status=ServiceRequestDispatch.STATUS_QUEUED,
        next_attempt_at=now,
    )


def _process_dispatch(dispatch_id) -> str:
    prepared = _claim_or_close(dispatch_id)
    if prepared is None:
        return 'ignored'
    action, claimed_id = prepared
    if action != 'claimed':
        return action
    _deliver_claimed_dispatch(claimed_id)
    dispatch = ServiceRequestDispatch.objects.get(pk=claimed_id)
    if dispatch.status == ServiceRequestDispatch.STATUS_SENT:
        return 'sent'
    if dispatch.status == ServiceRequestDispatch.STATUS_FAILED:
        return 'failed'
    return 'retried'


def _claim_or_close(dispatch_id):
    with transaction.atomic():
        dispatch = (
            ServiceRequestDispatch.objects.select_for_update()
            .select_related('request', 'seller')
            .filter(pk=dispatch_id)
            .first()
        )
        if dispatch is None or dispatch.status != ServiceRequestDispatch.STATUS_QUEUED:
            return None
        if dispatch.next_attempt_at > timezone.now():
            return None

        success_log = _successful_attempt_log(dispatch)
        if success_log is not None:
            dispatch.status = ServiceRequestDispatch.STATUS_SENT
            dispatch.sent_at = dispatch.sent_at or timezone.now()
            if success_log.meta_message_id:
                dispatch.provider_message_id = success_log.meta_message_id
            dispatch.save(update_fields=[
                'status',
                'sent_at',
                'provider_message_id',
                'updated_at',
            ])
            return ('reconciled', dispatch.pk)

        skip_reason = _runtime_skip_reason(dispatch)
        if skip_reason:
            dispatch.status = ServiceRequestDispatch.STATUS_SKIPPED
            dispatch.skip_reason = skip_reason
            dispatch.save(update_fields=[
                'status',
                'skip_reason',
                'updated_at',
            ])
            return ('skipped', dispatch.pk)

        dispatch.status = ServiceRequestDispatch.STATUS_PROCESSING
        dispatch.attempts_count += 1
        dispatch.last_attempt_at = timezone.now()
        dispatch.save(update_fields=[
            'status',
            'attempts_count',
            'last_attempt_at',
            'updated_at',
        ])
        return ('claimed', dispatch.pk)


def _runtime_skip_reason(dispatch) -> str:
    mode = get_service_broadcast_mode()
    if mode != ServiceBroadcastSettings.MODE_LIVE and mode != ServiceBroadcastSettings.MODE_TEST:
        return SKIP_BROADCAST_DISABLED

    seller = dispatch.seller
    if not seller.is_active:
        return SKIP_SELLER_INACTIVE
    if not seller.receive_requests:
        return SKIP_RECEIVE_REQUESTS_DISABLED
    if seller.is_paused:
        return SKIP_SELLER_PAUSED
    if mode == ServiceBroadcastSettings.MODE_TEST and not seller.is_test_seller:
        return SKIP_NOT_TEST_SELLER
    return ''


def _successful_attempt_log(dispatch):
    return (
        ServiceWhatsAppMessageLog.objects.filter(
            request_id=dispatch.request_id,
            seller_id=dispatch.seller_id,
            message_type='seller_request',
            status='sent',
        )
        .order_by('-pk')
        .first()
    )


def _deliver_claimed_dispatch(dispatch_id):
    dispatch = (
        ServiceRequestDispatch.objects.select_related('request', 'seller')
        .get(pk=dispatch_id)
    )
    try:
        result = send_whatsapp_template_message(
            **service_request_template_call(dispatch.request, dispatch.seller),
        )
    except Exception as exc:
        logger.exception(
            'Service request WhatsApp send failed for dispatch #%s',
            dispatch_id,
        )
        result = {
            'ok': False,
            'message_id': '',
            'error': redact_whatsapp_sensitive_data(str(exc)),
            'response': '',
        }
    _write_attempt_log(dispatch, result)
    _finish_attempt(dispatch.pk, result)


def _write_attempt_log(dispatch, result):
    ok = bool(result.get('ok'))
    message_id = result.get('message_id') or ''
    stored = _stored_provider_payload(result)
    ServiceWhatsAppMessageLog.objects.create(
        seller_id=dispatch.seller_id,
        request_id=dispatch.request_id,
        phone=normalize_whatsapp_phone(dispatch.seller.whatsapp),
        message_type='seller_request',
        status='sent' if ok else 'failed',
        meta_message_id=message_id,
        error_text='' if ok else sanitize_persisted_whatsapp_error_message(
            result.get('error') or stored,
        ),
        response_json=stored,
    )


def _stored_provider_payload(result) -> str:
    raw = result.get('response') if result.get('ok') else result.get('error')
    if raw in (None, ''):
        raw = result.get('response') or ''
    safe = redact_whatsapp_sensitive_data(raw)
    if isinstance(safe, str):
        return safe
    return json.dumps(safe, ensure_ascii=False)


def _finish_attempt(dispatch_id, result):
    with transaction.atomic():
        dispatch = (
            ServiceRequestDispatch.objects.select_for_update()
            .get(pk=dispatch_id)
        )
        if dispatch.status != ServiceRequestDispatch.STATUS_PROCESSING:
            return
        if result.get('ok'):
            dispatch.status = ServiceRequestDispatch.STATUS_SENT
            dispatch.sent_at = timezone.now()
            dispatch.provider_message_id = result.get('message_id') or ''
            dispatch.last_error = ''
            dispatch.save(update_fields=[
                'status',
                'sent_at',
                'provider_message_id',
                'last_error',
                'updated_at',
            ])
            return

        dispatch.last_error = sanitize_persisted_whatsapp_error_message(
            result.get('error') or '',
        )
        if dispatch.attempts_count >= MAX_ATTEMPTS:
            dispatch.status = ServiceRequestDispatch.STATUS_FAILED
            dispatch.save(update_fields=[
                'status',
                'last_error',
                'updated_at',
            ])
            return

        delay = RETRY_DELAYS_SECONDS[dispatch.attempts_count - 1]
        dispatch.status = ServiceRequestDispatch.STATUS_QUEUED
        dispatch.next_attempt_at = timezone.now() + timedelta(seconds=delay)
        dispatch.save(update_fields=[
            'status',
            'last_error',
            'next_attempt_at',
            'updated_at',
        ])
