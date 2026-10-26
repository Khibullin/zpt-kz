"""Regression coverage for safe Telegram verification and opt-in test."""
import os
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from core.services.telegram_channel import (
    REQUEST_TOPIC_ID,
    BOT_USERNAME,
    CHANNEL,
    TEST_MESSAGE,
    TelegramChannelError,
    send_test_post,
    verify_channel,
)

FAKE_TOKEN = "1234567890:abcdefghijklmnopqrstuvwxyzABCDEFGHI"


def response(result, status=200):
    value = Mock()
    value.ok = status < 400
    value.status_code = status
    value.json.return_value = {"ok": status < 400, "result": result}
    return value


def verify_responses():
    return [
        response({"is_bot": True, "id": 123, "username": BOT_USERNAME}),
        response({"type": "supergroup", "id": int(CHANNEL), "is_forum": True}),
        response({"status": "administrator", "can_post_messages": True}),
    ]


@patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN})
class TelegramChannelTests(SimpleTestCase):
    @patch("core.services.telegram_channel.requests.post")
    def test_verification_is_read_only(self, post):
        post.side_effect = verify_responses()
        self.assertEqual(verify_channel(), {"bot": BOT_USERNAME, "channel": CHANNEL})
        self.assertEqual(
            [args.args[0].rsplit("/", 1)[-1] for args in post.call_args_list],
            ["getMe", "getChat", "getChatMember"],
        )

    @patch("core.services.telegram_channel.requests.post")
    def test_test_post_never_contains_customer_data(self, post):
        post.side_effect = verify_responses() + [response({"message_id": 42})]
        self.assertEqual(send_test_post(), 42)
        self.assertEqual(post.call_count, 4)
        self.assertTrue(post.call_args.args[0].endswith("/sendMessage"))
        params = post.call_args.kwargs["json"]
        self.assertEqual(params["chat_id"], CHANNEL)
        self.assertEqual(params["message_thread_id"], REQUEST_TOPIC_ID)
        self.assertEqual(params["text"], TEST_MESSAGE)
        self.assertNotIn("phone", params)
        self.assertEqual(params["link_preview_options"], {"is_disabled": True})

    @patch("core.services.telegram_channel.requests.post")
    def test_wrong_bot_fails_closed(self, post):
        post.return_value = response({
            "is_bot": True, "id": 123, "username": "other_bot",
        })
        with self.assertRaises(TelegramChannelError):
            verify_channel()
        self.assertEqual(post.call_count, 1)

    @patch("core.services.telegram_channel.requests.post")
    def test_wrong_channel_fails_closed(self, post):
        replies = verify_responses()
        replies[1] = response({"type": "channel", "username": "other_channel"})
        post.side_effect = replies
        with self.assertRaises(TelegramChannelError):
            verify_channel()
        self.assertEqual(post.call_count, 2)

    @patch("core.services.telegram_channel.requests.post")
    def test_missing_post_right_fails_closed(self, post):
        replies = verify_responses()
        replies[2] = response({"status": "left"})
        post.side_effect = replies
        with self.assertRaises(TelegramChannelError):
            verify_channel()
        self.assertEqual(post.call_count, 3)

    @patch("core.services.telegram_channel.requests.post")
    def test_http_error_does_not_expose_token(self, post):
        value = response(None, 401)
        value.json.return_value = {
            "ok": False,
            "error_code": 401,
            "description": "private " + FAKE_TOKEN,
        }
        post.return_value = value
        with self.assertRaises(TelegramChannelError) as caught:
            verify_channel()
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))

    @patch("core.services.telegram_channel.requests.post")
    def test_connection_error_does_not_expose_token(self, post):
        post.side_effect = __import__("requests").ConnectionError(
            "https://api.telegram.org/bot" + FAKE_TOKEN
        )
        with self.assertRaises(TelegramChannelError) as caught:
            verify_channel()
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))

    @patch("core.management.commands.check_zpt_telegram.send_test_post")
    @patch("core.management.commands.check_zpt_telegram.verify_channel")
    def test_command_does_not_send_by_default(self, verify, send):
        verify.return_value = {"bot": BOT_USERNAME, "channel": CHANNEL}
        output = StringIO()
        call_command("check_zpt_telegram", stdout=output)
        verify.assert_called_once_with()
        send.assert_not_called()
        self.assertIn("Никаких сообщений не отправлено", output.getvalue())

    @patch("core.management.commands.check_zpt_telegram.send_test_post")
    @patch("core.management.commands.check_zpt_telegram.verify_channel")
    def test_command_can_send_only_with_flag(self, verify, send):
        send.return_value = 42
        output = StringIO()
        call_command("check_zpt_telegram", "--send-test", stdout=output)
        verify.assert_not_called()
        send.assert_called_once_with()
        self.assertIn("message_id=42", output.getvalue())


class TelegramTokenMissingTests(SimpleTestCase):
    @patch.dict(os.environ, {}, clear=True)
    @patch("core.services.telegram_channel.requests.post")
    def test_missing_token_does_not_call_api(self, post):
        with self.assertRaises(TelegramChannelError):
            verify_channel()
        post.assert_not_called()


class CommunityLinksTests(SimpleTestCase):
    def test_destination_specific_message_links(self):
        from core.models import TelegramRequestPublication
        pub = TelegramRequestPublication(message_id=42, chat_id='-1004326627260')
        self.assertEqual(pub.message_url, 'https://t.me/c/4326627260/42')
        pub.chat_id = '@zpt_kz_zakaz'
        self.assertEqual(pub.message_url, 'https://t.me/zpt_kz_zakaz/42')

    def test_reconcile_accepts_only_our_destinations(self):
        from core.workflow_admin import ReconcileForm
        for url in ['https://t.me/zpt_kz_parts/42', 'https://t.me/c/4326627260/42']:
            form = ReconcileForm({'url':url, 'reviewed':True})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.destination, '-1004326627260')
        self.assertFalse(ReconcileForm({'url':'https://t.me/other/42', 'reviewed':True}).is_valid())
