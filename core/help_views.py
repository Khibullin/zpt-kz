from __future__ import annotations

import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core.models import PlatformHelpMessage
from core.platform_help import (
    AUDIO_MAX_BYTES,
    ALLOWED_AUDIO_CONTENT_TYPES,
    PlatformHelpError,
    RATE_LIMIT_MESSAGE,
    SESSION_CONVERSATION_KEY,
    SAFE_ASK_UNAVAILABLE,
    SAFE_TRANSCRIBE_UNAVAILABLE,
    answer_platform_help,
    conversation_history_payload,
    get_or_create_conversation,
    load_conversation_from_session,
    normalize_audio_content_type,
    normalize_input_mode,
    rate_limit_allowed,
    transcribe_help_audio,
    validate_question,
)
from core.services.platform_help_email import notify_platform_help_question_safely


def _json_error(message: str, status: int) -> JsonResponse:
    return JsonResponse({'ok': False, 'message': message}, status=status)


def _safe_error(exc: Exception, fallback: str, status: int = 503) -> JsonResponse:
    if isinstance(exc, PlatformHelpError):
        return _json_error(exc.message, exc.status)
    return _json_error(fallback, status)


@ensure_csrf_cookie
@require_GET
def platform_help_page(request):
    return render(request, 'request-parts/help/index.html')


@require_POST
def platform_help_ask(request):
    if not rate_limit_allowed('ask', request):
        return _json_error(RATE_LIMIT_MESSAGE, 429)
    try:
        payload = json.loads(request.body.decode('utf-8') or '')
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _json_error('Некорректный запрос.', 400)
    if not isinstance(payload, dict):
        return _json_error('Некорректный запрос.', 400)
    try:
        question = validate_question(payload.get('message'))
    except PlatformHelpError as exc:
        return _json_error(exc.message, exc.status)
    input_mode = normalize_input_mode(payload.get('input_mode'))
    conversation = get_or_create_conversation(request)
    history_rows = list(
        conversation.messages.order_by('created_at', 'id')
    )
    user_message = PlatformHelpMessage.objects.create(
        conversation=conversation,
        role=PlatformHelpMessage.ROLE_USER,
        input_mode=input_mode,
        content=question,
    )
    conversation.save(update_fields=['updated_at'])
    try:
        answer = answer_platform_help(question, history_rows)
    except Exception as exc:
        notify_platform_help_question_safely(
            request,
            user_message,
            ai_failed=True,
        )
        return _safe_error(exc, SAFE_ASK_UNAVAILABLE, 503)
    model = str(getattr(settings, 'HELP_AI_MODEL', '') or '').strip()
    PlatformHelpMessage.objects.create(
        conversation=conversation,
        role=PlatformHelpMessage.ROLE_ASSISTANT,
        input_mode=PlatformHelpMessage.MODE_TEXT,
        content=answer,
        ai_model=model,
    )
    conversation.updated_at = timezone.now()
    conversation.save(update_fields=['updated_at'])
    notify_platform_help_question_safely(
        request,
        user_message,
        answer=answer,
    )
    return JsonResponse({
        'ok': True,
        'answer': answer,
        'input_mode': input_mode,
    })


@require_POST
def platform_help_transcribe(request):
    if not rate_limit_allowed('transcribe', request):
        return _json_error(RATE_LIMIT_MESSAGE, 429)
    uploaded = request.FILES.get('audio') or request.FILES.get('file')
    if uploaded is None:
        return _json_error('Добавьте голосовую запись.', 400)
    if getattr(uploaded, 'size', 0) > AUDIO_MAX_BYTES:
        return _json_error('Голосовая запись слишком большая.', 413)
    content_type = normalize_audio_content_type(
        getattr(uploaded, 'content_type', '') or request.POST.get('content_type', '')
    )
    if content_type not in ALLOWED_AUDIO_CONTENT_TYPES:
        return _json_error('Этот формат аудио не поддерживается.', 400)
    filename = str(getattr(uploaded, 'name', '') or 'help-audio.webm')
    file_bytes = uploaded.read()
    if not file_bytes:
        return _json_error('Добавьте голосовую запись.', 400)
    if len(file_bytes) > AUDIO_MAX_BYTES:
        return _json_error('Голосовая запись слишком большая.', 413)
    try:
        text = transcribe_help_audio(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )
    except Exception as exc:
        return _safe_error(exc, SAFE_TRANSCRIBE_UNAVAILABLE, 503)
    return JsonResponse({'ok': True, 'text': text})


@require_GET
def platform_help_history(request):
    conversation = load_conversation_from_session(request)
    if conversation is None:
        return JsonResponse({'ok': True, 'messages': []})
    return JsonResponse({
        'ok': True,
        'messages': conversation_history_payload(conversation),
    })


@require_POST
def platform_help_new_conversation(request):
    request.session.pop(SESSION_CONVERSATION_KEY, None)
    request.session.modified = True
    return JsonResponse({'ok': True})
