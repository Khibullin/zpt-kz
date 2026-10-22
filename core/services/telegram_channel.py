"""ZPT.KZ Telegram channel: read-only verification and opt-in test post.

No customer requests, personal data or automatic publishing here.
"""
from __future__ import annotations

import os

import requests


BOT_USERNAME = "zpt_kz_zakaz_bot"
CHANNEL = "@zpt_kz_zakaz"
TEST_MESSAGE = (
    "✅ ZPT.KZ — тест подключения Telegram.\n"
    "Это техническое сообщение, не заявка покупателя.\n"
    "Автоматическая публикация заявок пока отключена."
)


class TelegramChannelError(RuntimeError):
    """Safe error message that does not disclose a token."""


def _call_api(method: str, params: dict) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or ":" not in token:
        raise TelegramChannelError(
            "TELEGRAM_BOT_TOKEN не задан или имеет неверный формат."
        )
    # Never log HTTP request URLs: Telegram embeds the token in the URL.
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=params,
            timeout=10,
        )
    except requests.RequestException:
        raise TelegramChannelError(
            "Не удалось связаться с Telegram API (подробности скрыты)."
        ) from None

    try:
        data = response.json()
    except ValueError:
        raise TelegramChannelError("Некорректный ответ Telegram API.") from None
    if not response.ok or not isinstance(data, dict) or data.get("ok") is not True:
        code = data.get("error_code") if isinstance(data, dict) else None
        if not isinstance(code, int):
            code = response.status_code
        raise TelegramChannelError(
            f"Telegram API: ошибка {code}; проверьте токен и права бота."
        )
    result = data.get("result")
    if not isinstance(result, dict):
        raise TelegramChannelError("Неожиданный формат ответа Telegram API.")
    return result


def verify_channel() -> dict:
    """Verify target bot/channel/admin rights without sending messages."""
    bot = _call_api("getMe", {})
    if (
        bot.get("is_bot") is not True
        or str(bot.get("username", "")).casefold() != BOT_USERNAME
        or not isinstance(bot.get("id"), int)
    ):
        raise TelegramChannelError(
            "Токен принадлежит другому боту; отправка запрещена."
        )

    chat = _call_api("getChat", {"chat_id": CHANNEL})
    if (
        chat.get("type") != "channel"
        or str(chat.get("username", "")).casefold() != CHANNEL[1:]
    ):
        raise TelegramChannelError("Целевой канал ZPT.KZ не подтверждён.")

    member = _call_api(
        "getChatMember",
        {"chat_id": CHANNEL, "user_id": bot["id"]},
    )
    if member.get("status") != "administrator" or member.get("can_post_messages") is not True:
        raise TelegramChannelError("У бота нет права Post Messages.")
    return {"bot": BOT_USERNAME, "channel": CHANNEL}


def send_test_post() -> int:
    """Only a fixed technical test post, when called explicitly."""
    verify_channel()
    result = _call_api("sendMessage", {
        "chat_id": CHANNEL,
        "text": TEST_MESSAGE,
        "link_preview_options": {"is_disabled": True},
    })
    message_id = result.get("message_id")
    if not isinstance(message_id, int):
        raise TelegramChannelError("Telegram не подтвердил публикацию.")
    return message_id
