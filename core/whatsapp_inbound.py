from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_SOURCE_WHATSAPP,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    INBOUND_EVENT_STATUS_AMBIGUOUS,
    INBOUND_EVENT_STATUS_ERROR,
    INBOUND_EVENT_STATUS_IGNORED,
    INBOUND_EVENT_STATUS_PROCESSED,
    INBOUND_EVENT_STATUS_STALE,
    INBOUND_EVENT_STATUS_UNMATCHED,
    SELLER_CONFIRM_ACTION_NO,
    SELLER_CONFIRM_ACTION_YES,
    SELLER_CONFIRM_NO_TEXT,
    SELLER_CONFIRM_YES_TEXT,
    SELLER_PLATFORM_CONFIRM_TEMPLATE,
    Seller,
    SellerContactConsent,
    WhatsAppInboundEvent,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_identity import find_sellers_by_phone

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r'\s+')
_YES_NORMALIZED = _WHITESPACE_RE.sub(' ', SELLER_CONFIRM_YES_TEXT.replace('\u00a0', ' ')).strip().casefold()
_NO_NORMALIZED = _WHITESPACE_RE.sub(' ', SELLER_CONFIRM_NO_TEXT.replace('\u00a0', ' ')).strip().casefold()


def normalize_quick_reply_text(value: object) -> str:
    text = str(value or '').replace('\u00a0', ' ').replace('\u202f', ' ')
    return _WHITESPACE_RE.sub(' ', text).strip().casefold()


def resolve_seller_confirm_action(button_text: object) -> str:
    normalized = normalize_quick_reply_text(button_text)
    if not normalized:
        return ''
    if normalized == _YES_NORMALIZED:
        return SELLER_CONFIRM_ACTION_YES
    if normalized == _NO_NORMALIZED:
        return SELLER_CONFIRM_ACTION_NO
    return ''


def hash_payload(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body or b'').hexdigest()


def signatures_match(raw_body: bytes, header_value: str, secret: str) -> bool:
    if not secret:
        return False
    header = str(header_value or '').strip()
    prefix = 'sha256='
    if not header.lower().startswith(prefix):
        return False
    provided = header.split('=', 1)[1].strip()
    if not provided:
        return False
    expected = hmac.new(
        secret.encode('utf-8'),
        raw_body or b'',
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided)


def verify_tokens_match(provided: str, expected: str) -> bool:
    left = str(provided or '')
    right = str(expected or '')
    if not left or not right:
        return False
    return hmac.compare_digest(left.encode('utf-8'), right.encode('utf-8'))


def extract_quick_reply(message: dict) -> tuple[str, str]:
    msg_type = str(message.get('type') or '')
    if msg_type == 'button':
        button = message.get('button')
        text = ''
        if isinstance(button, dict):
            text = str(button.get('text') or '')
        return msg_type, text
    if msg_type == 'interactive':
        interactive = message.get('interactive')
        if isinstance(interactive, dict) and interactive.get('type') == 'button_reply':
            reply = interactive.get('button_reply')
            text = ''
            if isinstance(reply, dict):
                text = str(reply.get('title') or '')
            return msg_type, text
        return msg_type, ''
    return msg_type, ''


def _parse_provider_timestamp(raw_value: object):
    try:
        unix_ts = int(str(raw_value or '').strip())
    except (TypeError, ValueError):
        return None
    if unix_ts <= 0:
        return None
    return datetime.fromtimestamp(unix_ts, tz=dt_timezone.utc)


def iter_inbound_messages(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    messages: list[dict] = []
    for entry in payload.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get('changes') or []:
            if not isinstance(change, dict):
                continue
            value = change.get('value')
            if not isinstance(value, dict):
                continue
            for message in value.get('messages') or []:
                if isinstance(message, dict):
                    messages.append(message)
    return messages


def process_whatsapp_webhook_payload(payload: Any, *, raw_body: bytes) -> int:
    payload_hash = hash_payload(raw_body)
    processed = 0
    for message in iter_inbound_messages(payload):
        if process_inbound_message(message, payload_hash=payload_hash):
            processed += 1
    return processed


def process_inbound_message(message: dict, *, payload_hash: str) -> bool:
    provider_message_id = str(message.get('id') or '').strip()
    if not provider_message_id:
        return False
    if WhatsAppInboundEvent.objects.filter(provider_message_id=provider_message_id).exists():
        return False

    message_type, button_text = extract_quick_reply(message)
    action = resolve_seller_confirm_action(button_text) if button_text else ''
    if message_type not in {'button', 'interactive'}:
        action = ''
    phone_normalized = normalize_kz_phone(message.get('from')) or ''
    provider_timestamp = _parse_provider_timestamp(message.get('timestamp'))
    event_time = provider_timestamp or timezone.now()
    event = WhatsAppInboundEvent(
        provider_message_id=provider_message_id,
        phone_normalized=phone_normalized,
        message_type=message_type,
        button_text=str(button_text or '')[:255],
        action=action,
        processing_status=INBOUND_EVENT_STATUS_IGNORED,
        provider_timestamp=provider_timestamp,
        payload_hash=payload_hash,
    )

    try:
        with transaction.atomic():
            _apply_inbound_event(event, event_time=event_time)
            event.save()
            return True
    except IntegrityError:
        if WhatsAppInboundEvent.objects.filter(provider_message_id=provider_message_id).exists():
            return False
        logger.exception(
            'whatsapp inbound processing failed message_id=%s',
            provider_message_id,
        )
        _persist_error_inbound_event(event)
        return False
    except Exception:
        logger.exception(
            'whatsapp inbound processing failed message_id=%s',
            provider_message_id,
        )
        _persist_error_inbound_event(event)
        return False


def _persist_error_inbound_event(event: WhatsAppInboundEvent) -> None:
    defaults = {
        'phone_normalized': event.phone_normalized,
        'message_type': event.message_type,
        'button_text': event.button_text,
        'action': event.action,
        'seller': event.seller if event.seller_id else None,
        'processing_status': INBOUND_EVENT_STATUS_ERROR,
        'provider_timestamp': event.provider_timestamp,
        'payload_hash': event.payload_hash,
        'processed_at': timezone.now(),
    }
    try:
        WhatsAppInboundEvent.objects.get_or_create(
            provider_message_id=event.provider_message_id,
            defaults=defaults,
        )
    except IntegrityError:
        return


def _apply_inbound_event(event: WhatsAppInboundEvent, *, event_time) -> None:
    now = timezone.now()
    if event.message_type not in {'button', 'interactive'} or not event.action:
        event.processing_status = INBOUND_EVENT_STATUS_IGNORED
        event.processed_at = now
        return

    if not event.phone_normalized:
        event.processing_status = INBOUND_EVENT_STATUS_UNMATCHED
        event.processed_at = now
        return

    sellers = find_sellers_by_phone(event.phone_normalized)
    if not sellers:
        event.processing_status = INBOUND_EVENT_STATUS_UNMATCHED
        event.processed_at = now
        return
    if len(sellers) > 1:
        event.processing_status = INBOUND_EVENT_STATUS_AMBIGUOUS
        event.processed_at = now
        return

    seller = Seller.objects.select_for_update().get(pk=sellers[0].pk)
    event.seller = seller
    if event.action not in {SELLER_CONFIRM_ACTION_YES, SELLER_CONFIRM_ACTION_NO}:
        event.processing_status = INBOUND_EVENT_STATUS_IGNORED
        event.processed_at = now
        return

    consent = _consent_lookup(seller, event.phone_normalized)
    skip_status = _incoming_decision_skip_status(consent, event_time, event.action)
    if skip_status:
        event.processing_status = skip_status
        event.processed_at = now
        return

    if event.action == SELLER_CONFIRM_ACTION_YES:
        _apply_seller_confirm_yes(seller, event, consent=consent, event_time=event_time)
    else:
        _apply_seller_confirm_no(seller, event, consent=consent, event_time=event_time)

    event.processing_status = INBOUND_EVENT_STATUS_PROCESSED
    event.processed_at = now


def _consent_lookup(seller: Seller, phone_normalized: str) -> SellerContactConsent | None:
    return (
        SellerContactConsent.objects.select_for_update()
        .filter(
            seller=seller,
            phone_normalized=phone_normalized,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        .order_by('-updated_at', '-id')
        .first()
    )


def _latest_decision_at(consent: SellerContactConsent):
    timestamps = [
        value
        for value in (consent.consented_at, consent.revoked_at)
        if value is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _incoming_decision_skip_status(consent: SellerContactConsent | None, event_time, action: str) -> str:
    if consent is None:
        return ''
    latest = _latest_decision_at(consent)
    if latest is None:
        return ''
    if event_time < latest:
        return INBOUND_EVENT_STATUS_STALE
    if event_time == latest and action == SELLER_CONFIRM_ACTION_YES:
        return INBOUND_EVENT_STATUS_STALE
    return ''


def _apply_seller_confirm_yes(
    seller: Seller,
    event: WhatsAppInboundEvent,
    *,
    consent: SellerContactConsent | None,
    event_time,
) -> None:
    evidence = f'whatsapp:{event.provider_message_id}'
    if consent is None:
        consent = SellerContactConsent(
            seller=seller,
            phone_normalized=event.phone_normalized,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
    consent.status = CONTACT_CONSENT_STATUS_GRANTED
    consent.source = CONTACT_CONSENT_SOURCE_WHATSAPP
    consent.consent_text_version = SELLER_PLATFORM_CONFIRM_TEMPLATE
    consent.phone_normalized = event.phone_normalized
    consent.consented_at = event_time
    consent.revoked_at = None
    consent.evidence_reference = evidence
    consent.save()

    seller.receive_requests = True
    seller.is_paused = False
    seller.save(update_fields=['receive_requests', 'is_paused'])


def _apply_seller_confirm_no(
    seller: Seller,
    event: WhatsAppInboundEvent,
    *,
    consent: SellerContactConsent | None,
    event_time,
) -> None:
    evidence = f'whatsapp:{event.provider_message_id}'
    if consent is None:
        consent = SellerContactConsent(
            seller=seller,
            phone_normalized=event.phone_normalized,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
    consent.status = CONTACT_CONSENT_STATUS_REVOKED
    consent.source = CONTACT_CONSENT_SOURCE_WHATSAPP
    consent.consent_text_version = SELLER_PLATFORM_CONFIRM_TEMPLATE
    consent.phone_normalized = event.phone_normalized
    consent.revoked_at = event_time
    consent.evidence_reference = evidence
    consent.save()

    seller.receive_requests = False
    seller.is_paused = True
    seller.save(update_fields=['receive_requests', 'is_paused'])
