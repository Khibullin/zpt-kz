from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

from core.models import SellerLeadPipelineRun

PIPELINE_ADVISORY_LOCK_KEY = 8472916305
SQLITE_PIPELINE_LOCK = threading.Lock()

MAX_SAFE_MESSAGE_LENGTH = 1000
DEFAULT_COOLDOWN_MINUTES = 60
MAX_COOLDOWN_MINUTES = 10080

BRAVE_API_KEY_PATTERN = re.compile(r'BSA-[A-Za-z0-9-]+')

LIVE_COOLDOWN_STATUSES = (
    SellerLeadPipelineRun.STATUS_SUCCESS,
    SellerLeadPipelineRun.STATUS_PARTIAL,
)


class PipelineLockBusy(Exception):
    """Pipeline уже выполняется другим процессом."""


@dataclass(frozen=True)
class CooldownCheckResult:
    allowed: bool
    previous_run: SellerLeadPipelineRun | None = None
    minutes_remaining: int = 0


def _redact_sensitive_text(text: str) -> str:
    redacted = text
    api_key = (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip()
    if api_key:
        redacted = redacted.replace(api_key, '[REDACTED]')
    redacted = BRAVE_API_KEY_PATTERN.sub('[REDACTED]', redacted)
    for marker in ('X-Subscription-Token', 'Authorization', 'Cookie'):
        redacted = re.sub(
            rf'({re.escape(marker)}\s*[:=]\s*)(\S+)',
            r'\1[REDACTED]',
            redacted,
            flags=re.IGNORECASE,
        )
    return redacted


def truncate_safe_message(message: str, *, limit: int = MAX_SAFE_MESSAGE_LENGTH) -> str:
    text = _redact_sensitive_text((message or '').strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


def _is_postgresql() -> bool:
    return connection.vendor == 'postgresql'


def try_acquire_pipeline_lock() -> bool:
    if _is_postgresql():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [PIPELINE_ADVISORY_LOCK_KEY])
            row = cursor.fetchone()
            return bool(row and row[0])
    return SQLITE_PIPELINE_LOCK.acquire(blocking=False)


def release_pipeline_lock() -> None:
    if _is_postgresql():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [PIPELINE_ADVISORY_LOCK_KEY])
        return
    if SQLITE_PIPELINE_LOCK.locked():
        SQLITE_PIPELINE_LOCK.release()


class PipelineRunLock:
    """Контекстный менеджер блокировки pipeline. Освобождает lock в finally."""

    def __init__(self) -> None:
        self._acquired = False

    def __enter__(self) -> PipelineRunLock:
        self._acquired = try_acquire_pipeline_lock()
        if not self._acquired:
            raise PipelineLockBusy(
                'Pipeline уже выполняется другим процессом. Запуск пропущен.',
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._acquired:
            release_pipeline_lock()
            self._acquired = False


def validate_cooldown_minutes(cooldown_minutes: int) -> None:
    if cooldown_minutes < 0:
        raise ValueError('cooldown-minutes не может быть отрицательным.')
    if cooldown_minutes > MAX_COOLDOWN_MINUTES:
        raise ValueError(f'cooldown-minutes не может превышать {MAX_COOLDOWN_MINUTES}.')


def check_pipeline_cooldown(
    *,
    cooldown_minutes: int,
    force_run: bool,
) -> CooldownCheckResult:
    if force_run or cooldown_minutes == 0:
        return CooldownCheckResult(allowed=True)

    previous_run = (
        SellerLeadPipelineRun.objects.filter(
            is_dry_run=False,
            status__in=LIVE_COOLDOWN_STATUSES,
        )
        .order_by('-started_at')
        .first()
    )
    if previous_run is None:
        return CooldownCheckResult(allowed=True)

    elapsed = timezone.now() - previous_run.started_at
    cooldown_delta = timedelta(minutes=cooldown_minutes)
    if elapsed >= cooldown_delta:
        return CooldownCheckResult(allowed=True)

    remaining_seconds = (cooldown_delta - elapsed).total_seconds()
    minutes_remaining = max(1, int(remaining_seconds // 60) + (1 if remaining_seconds % 60 else 0))
    return CooldownCheckResult(
        allowed=False,
        previous_run=previous_run,
        minutes_remaining=minutes_remaining,
    )
