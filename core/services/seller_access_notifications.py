"""Durable owner-only email queue; never authenticates a seller or sends login tokens."""
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from core.models import SellerAccessClaim

logger = logging.getLogger(__name__)


def process_owner_notices(limit=10):
    if not getattr(settings, 'REQUEST_OWNER_EMAIL_ENABLED', False):
        return 0
    recipient = (getattr(settings, 'REQUEST_OWNER_EMAIL', '') or '').strip()
    if not recipient:
        logger.warning('Seller access owner notices: notification email is not configured')
        return 0
    ids = list(SellerAccessClaim.objects.filter(owner_notice_status='pending',
        status__in=['pending', 'clarify']).order_by('created_at').values_list('pk', flat=True)[:limit])
    sent = 0
    for pk in ids:
        # Claim before SMTP: concurrent workers cannot send the same notification.
        if not SellerAccessClaim.objects.filter(pk=pk, owner_notice_status='pending',
                status__in=['pending', 'clarify']).update(owner_notice_status='sending', owner_notice_recipient=recipient):
            continue
        claim = SellerAccessClaim.objects.select_related('seller').get(pk=pk)
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
        review_url = base + reverse('admin:core_selleraccessclaim_change', args=[claim.pk])
        message = '\n'.join([
            'Новый запрос на подтверждение магазина ZPT.KZ',
            f'Магазин: {claim.seller.name}', f'Город: {claim.seller.city}',
            f'Телефон из карточки: {claim.seller.whatsapp}',
            f'Запрос: {claim.pk}', '', 'Открыть запрос в админке:', review_url, '',
            'Проверьте представителя через сохранённый контакт магазина и сохраните решение.',
            'Доступ ещё не подтверждён. Это уведомление не содержит ссылки для входа.',
        ])
        try:
            accepted = send_mail(subject=f'ZPT.KZ: подтвердить магазин — {claim.seller.name}',
                message=message, from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER,
                recipient_list=[recipient], fail_silently=False)
            if not accepted:
                raise RuntimeError('Email backend did not accept the message')
        except Exception as exc:
            # Do not automatically resend an uncertain SMTP attempt.
            SellerAccessClaim.objects.filter(pk=pk, owner_notice_status='sending').update(owner_notice_status='failed')
            logger.warning('Seller access owner notice failed: claim=%s error_type=%s smtp_code=%s', pk, type(exc).__name__, getattr(exc, 'smtp_code', 'n/a'))
        else:
            SellerAccessClaim.objects.filter(pk=pk, owner_notice_status='sending').update(
                owner_notice_status='sent', owner_notice_sent_at=timezone.now())
            logger.info('Seller access owner notice accepted by mail server: claim=%s', pk)
            sent += 1
    return sent
