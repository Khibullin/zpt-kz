from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from core.models import PlatformHelpConversation
from core.platform_help import build_help_whatsapp_reply_url
from core.services.seller_identity import get_logged_request_seller

logger = logging.getLogger(__name__)

AI_UNAVAILABLE_TEXT = (
    'Не получен: ИИ временно недоступен или произошла ошибка ответа.'
)


def mask_email(email: str) -> str:
    value = str(email or '').strip()
    if '@' not in value:
        return '***'
    local, domain = value.split('@', 1)
    if not local:
        return f'***@{domain}'
    return f'{local[0]}***@{domain}'


def get_help_notification_email() -> str:
    for candidate in (
        getattr(settings, 'HELP_NOTIFICATION_EMAIL', ''),
        getattr(settings, 'ORDER_ADMIN_EMAIL', ''),
        getattr(settings, 'EMAIL_HOST_USER', ''),
    ):
        email = str(candidate or '').strip()
        if email:
            return email
    return ''


def _dash(value: Any) -> str:
    text = str(value or '').strip()
    return text or '—'


def _input_mode_label(mode: str) -> str:
    if str(mode or '').strip() == 'voice':
        return 'голос'
    return 'текст'


def _admin_url(name: str, object_id: Any) -> str:
    try:
        path = reverse(name, args=[object_id])
    except NoReverseMatch:
        return ''
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    return f'{base}{path}'


def _format_created_at(value) -> str:
    if not value:
        return '—'
    local_value = timezone.localtime(value)
    return local_value.strftime('%d.%m.%Y %H:%M')


def _redact_secrets(text: str) -> str:
    redacted = str(text or '')
    for secret_name in ('EMAIL_HOST_PASSWORD', 'OPENAI_API_KEY'):
        secret = str(getattr(settings, secret_name, '') or '').strip()
        if secret:
            redacted = redacted.replace(secret, '[REDACTED]')
    return redacted[:200]


def build_platform_help_email_subject(seller=None) -> str:
    if seller is not None:
        return f'ZPT.KZ: новый вопрос продавца — {_dash(getattr(seller, "name", ""))}'
    return 'ZPT.KZ: новый вопрос — Вопросы и справки'


def _identity_lines(request, seller) -> list[str]:
    if seller is not None:
        return [
            'Тип пользователя: Продавец',
            f'Продавец: {_dash(getattr(seller, "name", ""))}',
            f'WhatsApp: {_dash(getattr(seller, "whatsapp", ""))}',
            f'Город: {_dash(getattr(seller, "city", ""))}',
        ]
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return [
            'Тип пользователя: Авторизованный пользователь',
            f'Пользователь: {_dash(user.get_username())}',
            f'Email пользователя: {_dash(getattr(user, "email", ""))}',
        ]
    return ['Тип пользователя: Анонимный пользователь']


def _contact_source_label(source: str) -> str:
    if source == PlatformHelpConversation.CONTACT_SOURCE_SELLER:
        return 'Профиль продавца'
    if source == PlatformHelpConversation.CONTACT_SOURCE_USER_INPUT:
        return 'Указан пользователем'
    return '—'


def _contact_lines(conversation) -> list[str]:
    digits = str(getattr(conversation, 'contact_whatsapp', '') or '').strip()
    if not digits:
        return ['WhatsApp для ответа: не указан']
    lines = [
        f'WhatsApp для ответа: +{digits}',
        f'Источник контакта: {_contact_source_label(getattr(conversation, "contact_source", ""))}',
    ]
    short_url = _short_whatsapp_url(digits)
    if short_url:
        lines.extend([
            '',
            'Ответить пользователю в WhatsApp:',
            short_url,
        ])
    return lines


def _short_whatsapp_url(digits: str) -> str:
    number = ''.join(ch for ch in str(digits or '') if ch.isdigit())
    if not number:
        return ''
    return f'https://wa.me/{number}'


def _html_multiline(value: Any) -> str:
    escaped = escape(str(value or ''))
    return mark_safe(escaped.replace('\n', '<br>'))


def _contact_digits(conversation) -> str:
    return str(getattr(conversation, 'contact_whatsapp', '') or '').strip()


def _identity_html(request, seller) -> list[str]:
    if seller is not None:
        return [
            mark_safe('<p>Тип пользователя: Продавец</p>'),
            format_html('<p>Продавец: {}</p>', _dash(getattr(seller, 'name', ''))),
            format_html('<p>WhatsApp: {}</p>', _dash(getattr(seller, 'whatsapp', ''))),
            format_html('<p>Город: {}</p>', _dash(getattr(seller, 'city', ''))),
        ]
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return [
            mark_safe('<p>Тип пользователя: Авторизованный пользователь</p>'),
            format_html('<p>Пользователь: {}</p>', _dash(user.get_username())),
            format_html(
                '<p>Email пользователя: {}</p>',
                _dash(getattr(user, 'email', '')),
            ),
        ]
    return [mark_safe('<p>Тип пользователя: Анонимный пользователь</p>')]


def _contact_html(conversation, digits: str) -> list[str]:
    if not digits:
        return [mark_safe('<p>WhatsApp для ответа: не указан</p>')]
    blocks = [
        format_html('<p>WhatsApp для ответа: +{}</p>', digits),
        format_html(
            '<p>Источник контакта: {}</p>',
            _contact_source_label(getattr(conversation, 'contact_source', '')),
        ),
    ]
    full_url = build_help_whatsapp_reply_url(digits)
    if full_url:
        blocks.append(
            format_html(
                '<p><a href="{}" style="display:inline-block;background:#25D366;'
                'color:#fff;padding:10px 16px;border-radius:8px;'
                'text-decoration:none;font-weight:700;">Ответить в WhatsApp</a></p>',
                full_url,
            )
        )
    return blocks


def build_platform_help_email_html(
    request,
    user_message,
    answer: str = '',
    ai_failed: bool = False,
    seller=None,
) -> str:
    conversation = user_message.conversation
    if ai_failed or not str(answer or '').strip():
        answer_text = AI_UNAVAILABLE_TEXT
    else:
        answer_text = str(answer).strip()

    conversation_url = _admin_url(
        'admin:core_platformhelpconversation_change',
        conversation.pk,
    )
    message_url = _admin_url(
        'admin:core_platformhelpmessage_change',
        user_message.pk,
    )
    digits = _contact_digits(conversation)

    blocks: list[str] = [
        mark_safe(
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            'line-height:1.5;color:#111;">'
        ),
        mark_safe(
            '<h2 style="margin:0 0 12px;">ZPT.KZ — новый вопрос через «Вопросы и справки»</h2>'
        ),
        *_identity_html(request, seller),
        *_contact_html(conversation, digits),
        format_html(
            '<p>Способ ввода: {}</p>',
            _input_mode_label(user_message.input_mode),
        ),
        format_html('<p>Время: {}</p>', _format_created_at(user_message.created_at)),
        format_html('<p>Conversation UUID: {}</p>', str(conversation.public_id)),
        mark_safe('<p><strong>ВОПРОС:</strong></p>'),
        format_html('<p>{}</p>', _html_multiline(user_message.content)),
        mark_safe('<p><strong>ОТВЕТ ИИ:</strong></p>'),
        format_html('<p>{}</p>', _html_multiline(answer_text)),
    ]
    if conversation_url:
        blocks.append(
            format_html(
                '<p>Открыть диалог в Django Admin:<br><a href="{}">{}</a></p>',
                conversation_url,
                conversation_url,
            )
        )
    if message_url:
        blocks.append(
            format_html(
                '<p>Открыть вопрос в Django Admin:<br><a href="{}">{}</a></p>',
                message_url,
                message_url,
            )
        )
    blocks.append(mark_safe('</div>'))
    return mark_safe(''.join(str(block) for block in blocks))


def build_platform_help_email_body(
    request,
    user_message,
    answer: str = '',
    ai_failed: bool = False,
    seller=None,
) -> str:
    conversation = user_message.conversation
    if ai_failed or not str(answer or '').strip():
        answer_text = AI_UNAVAILABLE_TEXT
    else:
        answer_text = str(answer).strip()

    conversation_url = _admin_url(
        'admin:core_platformhelpconversation_change',
        conversation.pk,
    )
    message_url = _admin_url(
        'admin:core_platformhelpmessage_change',
        user_message.pk,
    )

    lines = [
        'ZPT.KZ — новый вопрос через «Вопросы и справки»',
        '',
        *_identity_lines(request, seller),
        '',
        *_contact_lines(conversation),
        '',
        f'Способ ввода: {_input_mode_label(user_message.input_mode)}',
        f'Время: {_format_created_at(user_message.created_at)}',
        f'Conversation UUID: {conversation.public_id}',
        '',
        'ВОПРОС:',
        user_message.content,
        '',
        'ОТВЕТ ИИ:',
        answer_text,
        '',
    ]
    if conversation_url:
        lines.extend([
            'Открыть диалог в Django Admin:',
            conversation_url,
            '',
        ])
    if message_url:
        lines.extend([
            'Открыть вопрос в Django Admin:',
            message_url,
        ])
    return '\n'.join(lines).rstrip() + '\n'


def send_platform_help_question_notification(
    request,
    user_message,
    answer: str = '',
    ai_failed: bool = False,
) -> bool:
    recipient = get_help_notification_email()
    if not recipient:
        logger.warning(
            'Platform help email skipped: no recipient, message_id=%s',
            getattr(user_message, 'pk', None),
        )
        return False

    seller = get_logged_request_seller(request)
    subject = build_platform_help_email_subject(seller)
    body = build_platform_help_email_body(
        request,
        user_message,
        answer=answer,
        ai_failed=ai_failed,
        seller=seller,
    )
    html_body = build_platform_help_email_html(
        request,
        user_message,
        answer=answer,
        ai_failed=ai_failed,
        seller=seller,
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
            html_message=html_body,
        )
    except Exception as exc:
        logger.error(
            'Platform help email failed: message_id=%s, recipient=%s, error=%s',
            getattr(user_message, 'pk', None),
            mask_email(recipient),
            _redact_secrets(f'{type(exc).__name__}: {exc}'),
        )
        return False

    logger.info(
        'Platform help email sent: message_id=%s, recipient=%s',
        getattr(user_message, 'pk', None),
        mask_email(recipient),
    )
    return True


def notify_platform_help_question_safely(
    request,
    user_message,
    answer: str = '',
    ai_failed: bool = False,
) -> bool:
    try:
        if not getattr(settings, 'HELP_EMAIL_ENABLED', False):
            return False
        return send_platform_help_question_notification(
            request,
            user_message,
            answer=answer,
            ai_failed=ai_failed,
        )
    except Exception as exc:
        logger.error(
            'Platform help email failed: message_id=%s, error=%s',
            getattr(user_message, 'pk', None),
            _redact_secrets(f'{type(exc).__name__}: {exc}'),
        )
        return False
