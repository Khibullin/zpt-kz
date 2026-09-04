from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.models import PlatformHelpConversation, PlatformHelpMessage

logger = logging.getLogger(__name__)

OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
OPENAI_TRANSCRIPTIONS_URL = 'https://api.openai.com/v1/audio/transcriptions'
ASK_TIMEOUT_SECONDS = 30
TRANSCRIBE_TIMEOUT_SECONDS = 45
QUESTION_MAX_CHARS = 2000
HISTORY_MAX_MESSAGES = 12
MESSAGE_CONTEXT_MAX_CHARS = 1500
AUDIO_MAX_BYTES = 8 * 1024 * 1024
SESSION_CONVERSATION_KEY = 'platform_help_conversation_id'

SAFE_ASK_UNAVAILABLE = (
    'Сейчас не удалось получить ответ. Попробуйте ещё раз через минуту '
    'или напишите вопрос текстом.'
)
SAFE_TRANSCRIBE_UNAVAILABLE = (
    'Не удалось распознать голос. Попробуйте ещё раз или напишите вопрос текстом.'
)
RATE_LIMIT_MESSAGE = 'Слишком много запросов. Попробуйте немного позже.'
HELP_WHATSAPP_INVALID = 'Проверьте номер WhatsApp.'
HELP_WHATSAPP_REPLY_PREFILL = (
    'Здравствуйте! Вы задавали вопрос в разделе «Вопросы и справки» на ZPT.KZ. '
    'Отвечаем по вашему обращению.'
)

ALLOWED_AUDIO_CONTENT_TYPES = frozenset({
    'audio/webm',
    'video/webm',
    'audio/mp4',
    'video/mp4',
    'audio/ogg',
    'application/ogg',
    'audio/wav',
    'audio/x-wav',
    'audio/mpeg',
    'audio/mp3',
    'audio/m4a',
    'audio/x-m4a',
})

PLATFORM_HELP_SYSTEM_PROMPT = """Ты — справочный ИИ-помощник ZPT.KZ.
Твоя задача — помогать пользователям разобраться в работе платформы.

Отвечай на языке пользователя. По умолчанию — русский.
Будь кратким и практичным.

Никогда не утверждай, что видишь личный кабинет, персональные данные,
конкретные заявки, платежи или состояние аккаунта пользователя,
если эти данные явно не переданы тебе системой.

Не выдумывай функции ZPT.KZ.
Если факт отсутствует в данном контексте —
прямо скажи, что не можешь подтвердить его,
и предложи FAQ или обратную связь.

Известные стабильные разделы ZPT.KZ:
Заявки / кабинет продавца по заявкам:
https://zpt.kz/go/requests/
Добавить товар:
https://zpt.kz/go/add-product/
Оптовые товары:
https://zpt.kz/go/wholesale/
Каталог продавцов:
https://zpt.kz/go/sellers/
Справка:
https://zpt.kz/go/help/
Создать заявку покупателя:
https://zpt.kz/request-parts/
FAQ:
https://zpt.kz/request-parts/faq/
Обратная связь:
https://zpt.kz/feedback/
Вход в кабинет продавца:
https://zpt.kz/seller/login/
Кабинет продавца:
https://zpt.kz/seller/dashboard/
Профиль продавца:
https://zpt.kz/seller/profile/

Размещение товара:
продавец может добавить товар вручную или воспользоваться
ИИ-помощником по артикулу.
ИИ-помощник продавца может помочь подготовить:
- описание;
- применимость;
- двигатели;
- OEM/кросс-номера;
- подходящие фотографии.
Результат проверяет продавец перед сохранением.

Оптовые предложения и прайс-листы доступны через
https://zpt.kz/go/wholesale/

Если спрашивают о подборе конкретной запчасти или применимости,
не выдавай непроверенный ответ. Это не Buyer AI.
Объясни, что подбор запчастей и проверка применимости — отдельная функция,
и предложи создать заявку на https://zpt.kz/request-parts/ или открыть каталог.

Если спрашивают вопрос, не связанный с ZPT.KZ,
вежливо сообщи, что этот помощник предназначен для работы с ZPT.KZ.

Не обещай:
- гарантированное наличие;
- гарантированный ответ продавца;
- гарантированную цену;
- действия в аккаунте, которых ты фактически не выполнял.

Ответ: обычный текст.
Без HTML.
Без markdown-таблиц.
Ссылки допустимы, предпочтительно https://zpt.kz/...
"""


class PlatformHelpError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_audio_content_type(raw: str) -> str:
    return str(raw or '').split(';', 1)[0].strip().lower()


def parse_help_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ''
    text = str(payload.get('output_text') or '').strip()
    if text:
        return text
    chunks: list[str] = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            piece = content.get('text') or content.get('output_text')
            if piece:
                chunks.append(str(piece))
    return '\n'.join(chunks).strip()


def _openai_api_key() -> str:
    return str(getattr(settings, 'OPENAI_API_KEY', '') or '').strip()


def client_fingerprint(request) -> str:
    forwarded = str(request.META.get('HTTP_X_FORWARDED_FOR') or '')
    ip = forwarded.split(',')[0].strip() if forwarded else ''
    if not ip:
        ip = str(request.META.get('REMOTE_ADDR') or '').strip()
    user_agent = str(request.META.get('HTTP_USER_AGENT') or '')
    secret = str(getattr(settings, 'SECRET_KEY', '') or '')
    raw = f'{ip}\n{user_agent}\n{secret}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def rate_limit_allowed(kind: str, request) -> bool:
    if kind == 'ask':
        limit = int(getattr(settings, 'HELP_ASK_MAX_PER_HOUR', 30) or 30)
    else:
        limit = int(getattr(settings, 'HELP_TRANSCRIBE_MAX_PER_HOUR', 12) or 12)
    window = int(getattr(settings, 'HELP_RATE_LIMIT_WINDOW', 3600) or 3600)
    if limit <= 0:
        return True
    digest = client_fingerprint(request)
    cache_key = f'platform_help:{kind}:{digest}'
    current = cache.get(cache_key)
    if current is None:
        cache.set(cache_key, 1, window)
        return True
    if int(current) >= limit:
        return False
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window)
    return True


def validate_question(raw: Any) -> str:
    if not isinstance(raw, str):
        raise PlatformHelpError('Введите вопрос текстом.', 400)
    question = raw.strip()
    if not question:
        raise PlatformHelpError('Введите вопрос текстом.', 400)
    if len(question) > QUESTION_MAX_CHARS:
        raise PlatformHelpError(
            f'Вопрос слишком длинный. Максимум {QUESTION_MAX_CHARS} символов.',
            400,
        )
    return question


def normalize_input_mode(raw: Any) -> str:
    value = str(raw or '').strip().lower()
    if value == PlatformHelpMessage.MODE_VOICE:
        return PlatformHelpMessage.MODE_VOICE
    return PlatformHelpMessage.MODE_TEXT


def normalize_help_contact_whatsapp(raw: Any) -> str:
    if raw is None:
        return ''
    text = str(raw).strip()
    if not text:
        return ''
    digits = ''.join(ch for ch in text if ch.isdigit())
    if not digits:
        raise PlatformHelpError(HELP_WHATSAPP_INVALID, 400)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    if 11 <= len(digits) <= 15:
        return digits
    raise PlatformHelpError(HELP_WHATSAPP_INVALID, 400)


def build_help_whatsapp_reply_url(digits: str) -> str:
    number = ''.join(ch for ch in str(digits or '') if ch.isdigit())
    if not number:
        return ''
    return f'https://wa.me/{number}?text={quote(HELP_WHATSAPP_REPLY_PREFILL)}'


def apply_conversation_contact(request, conversation, payload: dict) -> None:
    from core.services.seller_identity import get_logged_request_seller

    seller = get_logged_request_seller(request)
    seller_whatsapp = ''
    if seller is not None:
        try:
            seller_whatsapp = normalize_help_contact_whatsapp(
                getattr(seller, 'whatsapp', '')
            )
        except PlatformHelpError:
            seller_whatsapp = ''
    if seller is not None and seller_whatsapp:
        conversation.contact_whatsapp = seller_whatsapp
        conversation.contact_source = PlatformHelpConversation.CONTACT_SOURCE_SELLER
        conversation.save(update_fields=['contact_whatsapp', 'contact_source', 'updated_at'])
        return

    raw = ''
    if isinstance(payload, dict) and 'contact_whatsapp' in payload:
        raw = payload.get('contact_whatsapp')
    normalized = normalize_help_contact_whatsapp(raw)
    if not normalized:
        return
    conversation.contact_whatsapp = normalized
    conversation.contact_source = PlatformHelpConversation.CONTACT_SOURCE_USER_INPUT
    conversation.save(update_fields=['contact_whatsapp', 'contact_source', 'updated_at'])


def _truncate(text: str) -> str:
    cleaned = str(text or '')
    if len(cleaned) <= MESSAGE_CONTEXT_MAX_CHARS:
        return cleaned
    return cleaned[:MESSAGE_CONTEXT_MAX_CHARS]


def load_conversation_from_session(request) -> PlatformHelpConversation | None:
    raw_id = request.session.get(SESSION_CONVERSATION_KEY)
    if not raw_id:
        return None
    return PlatformHelpConversation.objects.filter(public_id=raw_id).first()


def get_or_create_conversation(request) -> PlatformHelpConversation:
    conversation = load_conversation_from_session(request)
    if conversation is not None:
        user = getattr(request, 'user', None)
        if (
            user is not None
            and user.is_authenticated
            and conversation.user_id is None
        ):
            conversation.user = user
            conversation.save(update_fields=['user', 'updated_at'])
        return conversation
    user = getattr(request, 'user', None)
    conversation = PlatformHelpConversation.objects.create(
        user=user if user is not None and user.is_authenticated else None,
    )
    request.session[SESSION_CONVERSATION_KEY] = str(conversation.public_id)
    request.session.modified = True
    return conversation


def conversation_history_payload(conversation: PlatformHelpConversation) -> list[dict]:
    rows = list(
        conversation.messages.order_by('created_at', 'id').values(
            'role',
            'content',
            'input_mode',
            'created_at',
        )
    )
    payload = []
    for row in rows:
        created = row['created_at']
        payload.append({
            'role': row['role'],
            'content': row['content'],
            'input_mode': row['input_mode'],
            'created_at': timezone.localtime(created).isoformat() if created else '',
        })
    return payload


def build_ai_input(question: str, history_rows: list[PlatformHelpMessage]) -> list[dict]:
    items: list[dict] = [
        {'role': 'system', 'content': PLATFORM_HELP_SYSTEM_PROMPT},
    ]
    for row in history_rows[-HISTORY_MAX_MESSAGES:]:
        role = row.role if row.role in {'user', 'assistant'} else 'user'
        content = _truncate(row.content)
        if not content:
            continue
        items.append({'role': role, 'content': content})
    items.append({'role': 'user', 'content': question})
    return items


def answer_platform_help(
    question: str,
    history_rows: list[PlatformHelpMessage],
    *,
    post=None,
) -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)
    model = str(getattr(settings, 'HELP_AI_MODEL', '') or '').strip() or 'gpt-5.6-luna'
    payload = {
        'model': model,
        'input': build_ai_input(question, history_rows),
    }
    http_post = post or requests.post
    try:
        response = http_post(
            OPENAI_RESPONSES_URL,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            json=payload,
            timeout=ASK_TIMEOUT_SECONDS,
        )
    except (requests.RequestException, TimeoutError, OSError):
        logger.warning('Platform help OpenAI request failed')
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503) from None
    if getattr(response, 'status_code', 500) >= 400:
        logger.warning('Platform help OpenAI HTTP error')
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)
    try:
        body = response.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.warning('Platform help OpenAI returned invalid JSON')
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503) from None
    text = parse_help_output_text(body)
    if not text:
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)
    return text


def transcribe_help_audio(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    post=None,
) -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise PlatformHelpError(SAFE_TRANSCRIBE_UNAVAILABLE, 503)
    model = (
        str(getattr(settings, 'HELP_TRANSCRIBE_MODEL', '') or '').strip()
        or 'gpt-4o-mini-transcribe'
    )
    http_post = post or requests.post
    try:
        response = http_post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={'Authorization': f'Bearer {api_key}'},
            data={'model': model},
            files={'file': (filename, file_bytes, content_type)},
            timeout=TRANSCRIBE_TIMEOUT_SECONDS,
        )
    except (requests.RequestException, TimeoutError, OSError):
        logger.warning('Platform help transcription request failed')
        raise PlatformHelpError(SAFE_TRANSCRIBE_UNAVAILABLE, 503) from None
    if getattr(response, 'status_code', 500) >= 400:
        logger.warning('Platform help transcription HTTP error')
        raise PlatformHelpError(SAFE_TRANSCRIBE_UNAVAILABLE, 503)
    try:
        body = response.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.warning('Platform help transcription returned invalid JSON')
        raise PlatformHelpError(SAFE_TRANSCRIBE_UNAVAILABLE, 503) from None
    text = str((body or {}).get('text') or '').strip()
    if not text:
        raise PlatformHelpError(SAFE_TRANSCRIBE_UNAVAILABLE, 503)
    return text
