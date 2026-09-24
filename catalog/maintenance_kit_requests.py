"""Operator notice for maintenance-kit missing-car requests.

Does not dispatch sellers and does not create core.Request / Match rows.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger(__name__)


def build_kit_car_request_admin_url(car_request, request=None) -> str:
    path = reverse(
        'admin:catalog_maintenancekitcarrequest_change',
        args=[car_request.pk],
    )
    if request is not None:
        return request.build_absolute_uri(path)
    base = str(getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz') or '').rstrip('/')
    return f'{base}{path}'


def build_kit_car_request_email(car_request, *, admin_url: str) -> tuple[str, str]:
    vin = (car_request.vin or '').strip() or '—'
    phone = (car_request.phone or '').strip() or '—'
    subject = (
        f'Запрос комплекта ТО: {car_request.brand} {car_request.model} '
        f'{car_request.year}'
    )
    body = '\n'.join([
        f'Новая заявка на комплект ТО №{car_request.pk}',
        '',
        f'Марка: {car_request.brand}',
        f'Модель: {car_request.model}',
        f'Год: {car_request.year}',
        f'Двигатель: {car_request.engine}',
        f'VIN: {vin}',
        f'Телефон / WhatsApp: {phone}',
        '',
        'Открыть в Django Admin:',
        admin_url,
    ])
    return subject, body


def send_kit_car_request_notification(car_request, request=None) -> bool:
    from catalog.views import FEEDBACK_NOTIFY_EMAIL

    recipient = str(FEEDBACK_NOTIFY_EMAIL or '').strip()
    if not recipient:
        logger.warning(
            'FEEDBACK_NOTIFY_EMAIL is empty; skip kit car request email id=%s',
            car_request.pk,
        )
        return False

    admin_url = build_kit_car_request_admin_url(car_request, request)
    subject, body = build_kit_car_request_email(car_request, admin_url=admin_url)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(
        settings, 'EMAIL_HOST_USER', '',
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception(
            'Failed to send maintenance kit car request email id=%s',
            car_request.pk,
        )
        return False
