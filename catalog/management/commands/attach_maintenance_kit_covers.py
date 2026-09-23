from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.maintenance_kit_covers import (
    apply_cover_attach,
    format_cover_report,
    plan_cover_attach,
)


class Command(BaseCommand):
    help = (
        'Привязывает одно общее фото к уже существующим комплектам ТО. '
        'Источник: папки «Комплект 1/2/3» или slug. '
        'По умолчанию dry-run. Запись только с --apply. '
        'Файлы пишутся в MEDIA_ROOT через хранилище Django, '
        'без новых комплектов и без смены состава, цен и публикации.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            required=True,
            help='Каталог с папками Комплект 1, Комплект 2, Комплект 3',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Сохранить выбранные общие фото в записи комплектов',
        )

    def handle(self, *args, **options):
        source = Path(options['source']).expanduser()
        if not source.is_dir():
            raise CommandError(f'Каталог не найден: {source}')
        apply = bool(options['apply'])
        plans = plan_cover_attach(source)
        if apply:
            apply_cover_attach(plans)
        self.stdout.write(format_cover_report(plans, apply=apply))
