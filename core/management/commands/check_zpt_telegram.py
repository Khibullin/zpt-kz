"""Verify ZPT.KZ's Telegram connection; optional, explicit test publication."""
from django.core.management.base import BaseCommand, CommandError

from core.services.telegram_channel import (
    TelegramChannelError,
    send_test_post,
    verify_channel,
)


class Command(BaseCommand):
    help = (
        "Проверить подключение Telegram без публикаций. "
        "--send-test отправляет одно фиксированное тестовое сообщение."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--send-test",
            action="store_true",
            help="Явно разрешить публикацию одного технического теста в канале.",
        )

    def handle(self, *args, **options):
        try:
            if options["send_test"]:
                message_id = send_test_post()
                self.stdout.write(self.style.SUCCESS(
                    f"Тест опубликован в @zpt_kz_zakaz; message_id={message_id}."
                ))
            else:
                result = verify_channel()
                self.stdout.write(self.style.SUCCESS(
                    f"Подключение проверено: @{result['bot']} "
                    f"может публиковать в {result['channel']}. "
                    "Никаких сообщений не отправлено."
                ))
        except TelegramChannelError as exc:
            raise CommandError(str(exc)) from None
