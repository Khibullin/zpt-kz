from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

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
ZPT Гид:
https://zpt.kz/zpt-gid/
Справка:
https://zpt.kz/go/help/
Создать заявку покупателя:
https://zpt.kz/
FAQ:
https://zpt.kz/zpt-gid/#spravka
Обратная связь:
https://zpt.kz/zpt-gid/#svyaz
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

Помощь по сайту:
опирайся на проверенные FAQ ниже. Не выдумывай правила платформы.
Ты не видишь сайт пользователя, его кабинет и переписку вне этого диалога.

Поиск по публичному каталогу:
если спрашивают, есть ли запчасть, артикул или название в каталоге ZPT —
вызови search_public_catalog. Сообщай только то, что вернул инструмент.
Точное совпадение артикула важнее похожего названия.
Совпадение слов в названии товара не подтверждает совместимость с автомобилем.
Не обещай точный подбор по VIN, взаимозаменяемость и совместимость
без подтверждения продавца.
Не называй внутренние склады, PP1, PP2, себестоимость, закупочные цены
и данные продавцов.
Если инструмент вернул not_found: напиши «В каталоге ZPT не найден».
Не пиши, что такой запчасти не существует.
Если инструмент вернул ошибку: скажи, что сейчас не удалось проверить каталог.
Не выдавай ошибку проверки за отсутствие товаров.
Если наличие unknown / «Уточните наличие у продавца» — так и напиши.

Помощь с заявкой:
уточни название детали, марку, модель, год и при необходимости артикул.
Если покупатель сам назвал VIN для заявки, передай его в prepare_parts_request.
Не включай телефон и текст обращения в черновик.
VIN, телефон и текст обращения нельзя передавать в URL.
Когда данных достаточно, вызови prepare_parts_request.
Покупатель сам проверяет форму на главной и нажимает «Отправить запрос».
Ты не создаёшь заявку и не запускаешь рассылку продавцам.

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
Название раздела помощи: «ZPT Гид». Не пиши «GPT Гид» и не «GPT Guide».
Название этого окна: «ИИ-помощник». Ответ даёт ИИ и появляется здесь.
"""


HELP_TOOLS = [
    {
        'type': 'function',
        'name': 'search_public_catalog',
        'description': (
            'Search published ZPT catalog products by article or product name. '
            'Use when the user asks whether a part, SKU or name is in the ZPT catalog. '
            'Keep leading zeros and significant characters from the user query. '
            'Do not use this for how-to questions about the website.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': (
                        'Article and/or product name as given by the user. '
                        'Do not strip leading zeros.'
                    ),
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
    },
    {
        'type': 'function',
        'name': 'prepare_parts_request',
        'description': (
            'Prepare a draft for the homepage parts-request form. '
            'Call only after collecting the part name and, when needed, brand, model, year and VIN. '
            'Include VIN only if the buyer already provided it for the request. '
            'Never include phone or a private message. This does not submit the request.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'Part name or article for the homepage form.',
                },
                'brand': {
                    'type': 'string',
                    'description': 'Vehicle brand, if known.',
                },
                'model': {
                    'type': 'string',
                    'description': 'Vehicle model, if known.',
                },
                'year': {
                    'type': 'string',
                    'description': 'Vehicle year, if known.',
                },
                'vin': {
                    'type': 'string',
                    'description': (
                        'Vehicle VIN if the buyer already provided it. '
                        '6 to 17 letters and digits. Do not invent a VIN.'
                    ),
                },
            },
            'required': ['query'],
            'additionalProperties': False,
        },
    },
]
MAX_TOOL_ROUNDS = 4
REQUEST_QUERY_MAX = 500
REQUEST_FIELD_MAX = 80


class PlatformHelpError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class HelpAnswer:
    text: str
    request_draft: dict | None = None
    catalog_used: bool = False
    tool_error: bool = False


def _clip(value: Any, limit: int) -> str:
    return ' '.join(str(value or '').split())[:limit]


def _draft_vin(raw: Any) -> str:
    text = str(raw or '').strip().upper().replace(' ', '')
    if not text or not text.isalnum() or not (6 <= len(text) <= 17):
        return ''
    return text


def prepare_parts_request_draft(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    query = _clip(raw.get('query'), REQUEST_QUERY_MAX)
    if not query:
        return {
            'ok': False,
            'error': 'missing_query',
            'message': 'Нужно название или артикул запчасти.',
        }
    year = _clip(raw.get('year'), 4)
    if year and (not year.isdigit() or not (1950 <= int(year) <= 2100)):
        year = ''
    draft = {
        'query': query,
        'brand': _clip(raw.get('brand'), REQUEST_FIELD_MAX),
        'model': _clip(raw.get('model'), REQUEST_FIELD_MAX),
        'year': year,
        'vin': _draft_vin(raw.get('vin')),
    }
    return {
        'ok': True,
        'draft': draft,
        'message': (
            'Черновик заявки подготовлен. Покупатель должен проверить форму '
            'на главной и отправить её сам. Заявка ещё не создана.'
        ),
    }


def execute_help_tool(name: str, raw_arguments: Any) -> dict:
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            arguments = {}
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        arguments = {}

    if name == 'search_public_catalog':
        from catalog.guide_catalog_search import search_public_catalog

        query = arguments.get('query') or arguments.get('article') or arguments.get('name')
        return search_public_catalog(str(query or ''))
    if name == 'prepare_parts_request':
        return prepare_parts_request_draft(arguments)
    return {
        'ok': False,
        'error': 'unknown_tool',
        'message': 'Этот инструмент недоступен.',
    }


def extract_function_calls(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    calls = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        if item.get('type') not in {'function_call', 'tool_call'}:
            continue
        call_id = str(item.get('call_id') or item.get('id') or '').strip()
        name = str(item.get('name') or '').strip()
        if not call_id or not name:
            continue
        calls.append({
            'call_id': call_id,
            'name': name,
            'arguments': item.get('arguments') or item.get('input') or {},
            'raw': item,
        })
    return calls


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
    from core.phone_utils import build_whatsapp_url

    return build_whatsapp_url(digits, HELP_WHATSAPP_REPLY_PREFILL)


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


def platform_help_system_prompt() -> str:
    from core.zpt_guide_faq import faq_prompt_block

    return f'{PLATFORM_HELP_SYSTEM_PROMPT}\n\n{faq_prompt_block()}'


def build_ai_input(question: str, history_rows: list[PlatformHelpMessage]) -> list[dict]:
    items: list[dict] = [
        {'role': 'system', 'content': platform_help_system_prompt()},
    ]
    for row in history_rows[-HISTORY_MAX_MESSAGES:]:
        role = row.role if row.role in {'user', 'assistant'} else 'user'
        content = _truncate(row.content)
        if not content:
            continue
        items.append({'role': role, 'content': content})
    items.append({'role': 'user', 'content': question})
    return items


def _post_openai_response(http_post, api_key: str, payload: dict):
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
        return response.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.warning('Platform help OpenAI returned invalid JSON')
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503) from None


def answer_platform_help(
    question: str,
    history_rows: list[PlatformHelpMessage],
    *,
    post=None,
) -> HelpAnswer:
    api_key = _openai_api_key()
    if not api_key:
        raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)
    model = str(getattr(settings, 'HELP_AI_MODEL', '') or '').strip() or 'gpt-5.6-luna'
    http_post = post or requests.post
    input_items = build_ai_input(question, history_rows)
    result = HelpAnswer(text='')

    for _round in range(MAX_TOOL_ROUNDS + 1):
        payload = {
            'model': model,
            'input': input_items,
            'tools': HELP_TOOLS,
        }
        body = _post_openai_response(http_post, api_key, payload)
        calls = extract_function_calls(body)
        text = parse_help_output_text(body)
        if not calls:
            if not text:
                raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)
            result.text = text
            return result

        for call in calls:
            input_items.append(call['raw'])
            tool_payload = execute_help_tool(call['name'], call['arguments'])
            if call['name'] == 'search_public_catalog':
                result.catalog_used = True
                if not tool_payload.get('ok'):
                    result.tool_error = True
            if call['name'] == 'prepare_parts_request' and tool_payload.get('ok'):
                draft = tool_payload.get('draft')
                if isinstance(draft, dict):
                    result.request_draft = {
                        'query': str(draft.get('query') or ''),
                        'brand': str(draft.get('brand') or ''),
                        'model': str(draft.get('model') or ''),
                        'year': str(draft.get('year') or ''),
                        'vin': str(draft.get('vin') or ''),
                    }
            input_items.append({
                'type': 'function_call_output',
                'call_id': call['call_id'],
                'output': json.dumps(tool_payload, ensure_ascii=False),
            })

        if text and _round == MAX_TOOL_ROUNDS:
            result.text = text
            return result

    raise PlatformHelpError(SAFE_ASK_UNAVAILABLE, 503)


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
