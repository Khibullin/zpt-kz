from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.ag_parts_catalog_refs import (
    REQUIRED_BRAND_MODELS,
    STATUS_CREATED,
    STATUS_WOULD_CREATE,
    VERIFY_ONLY_BRAND_MODELS,
    ensure_ag_parts_catalog_refs,
)


class Command(BaseCommand):
    help = (
        'Добавляет только недостающие Brand/CarModel для импорта AG Parts '
        '(пилот + CONFIRMED refs первой партии). '
        'По умолчанию dry-run. Реальная запись только с --apply. '
        'Не создаёт Product, не переименовывает и не удаляет справочники. '
        'import_ag_parts по-прежнему не создаёт Brand/CarModel.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Создать отсутствующие Brand/CarModel. Без флага — только план.',
        )

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        self.stdout.write('=== AG Parts catalog refs ===')
        self.stdout.write(f"mode: {'apply' if apply else 'dry-run'}")
        self.stdout.write('--- verify (read-only, never created here) ---')
        for brand_name, model_names in VERIFY_ONLY_BRAND_MODELS:
            self.stdout.write(
                f'check {brand_name} / ' + ', '.join(model_names)
            )
        self.stdout.write('--- required ---')
        for brand_name, model_names in REQUIRED_BRAND_MODELS:
            self.stdout.write(f'{brand_name}: ' + ', '.join(model_names))
        self.stdout.write('--- plan ---')

        if apply:
            with transaction.atomic():
                lines = ensure_ag_parts_catalog_refs(apply=True)
        else:
            lines = ensure_ag_parts_catalog_refs(apply=False)

        for line in lines:
            self.stdout.write(line.format())

        created = sum(1 for line in lines if line.status == STATUS_CREATED)
        would_create = sum(1 for line in lines if line.status == STATUS_WOULD_CREATE)
        exists = sum(1 for line in lines if line.status == 'EXISTS')
        self.stdout.write('')
        self.stdout.write(
            'TOTALS  '
            f'EXISTS={exists} '
            f'WOULD_CREATE={would_create} '
            f'CREATED={created}'
        )
        if not apply:
            self.stdout.write('No DB writes. Re-run with --apply to create missing refs.')
