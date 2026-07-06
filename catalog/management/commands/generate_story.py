from django.core.management.base import BaseCommand, CommandError

from catalog.image_generator import (
    ACTIVE_REQUEST_STATUSES,
    InstagramStoryGenerationError,
    generate_instagram_story,
    instagram_story_exists,
)
from core.models import Request


class Command(BaseCommand):
    help = (
        'Генерирует Instagram Story для заявок на запчасти. '
        'С аргументом --request_id — только для одной заявки, '
        'без аргумента — для последних 5 активных заявок без готовых файлов.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--request_id',
            type=int,
            help='ID конкретной заявки core.models.Request',
        )

    def handle(self, *args, **options):
        request_id = options.get('request_id')

        if request_id is not None:
            self._generate_for_request_id(request_id)
            return

        self._generate_for_recent_active_requests()

    def _generate_for_request_id(self, request_id: int) -> None:
        try:
            product_request = Request.objects.get(pk=request_id)
        except Request.DoesNotExist as exc:
            raise CommandError(f'Заявка с ID {request_id} не найдена.') from exc

        self.stdout.write(f'Генерация Instagram Story для заявки #{request_id}...')
        self._generate_and_report(product_request, force=True)

    def _generate_for_recent_active_requests(self) -> None:
        candidates = list(
            Request.objects.filter(status__in=ACTIVE_REQUEST_STATUSES)
            .order_by('-created_at')[:5]
        )

        if not candidates:
            self.stdout.write(self.style.WARNING('Активные заявки не найдены.'))
            return

        self.stdout.write(
            f'Найдено последних активных заявок для проверки: {len(candidates)}'
        )

        generated = 0
        skipped = 0
        failed = 0

        for product_request in candidates:
            if instagram_story_exists(product_request.pk):
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'  Пропуск заявки #{product_request.pk}: файл уже существует.'
                    )
                )
                continue

            result = self._generate_and_report(product_request, force=False)
            if result:
                generated += 1
            else:
                failed += 1

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'Готово. Создано: {generated}, пропущено: {skipped}, ошибок: {failed}.'
            )
        )

    def _generate_and_report(self, product_request: Request, *, force: bool) -> bool:
        request_id = product_request.pk

        if not force and instagram_story_exists(request_id):
            self.stdout.write(
                self.style.WARNING(
                    f'  Пропуск заявки #{request_id}: файл уже существует.'
                )
            )
            return False

        try:
            output_path = generate_instagram_story(product_request)
        except InstagramStoryGenerationError as exc:
            self.stdout.write(
                self.style.ERROR(
                    f'  Ошибка для заявки #{request_id}: {exc}'
                )
            )
            return False

        self.stdout.write(
            self.style.SUCCESS(
                f'  Заявка #{request_id}: карточка сохранена в {output_path}'
            )
        )
        return True
