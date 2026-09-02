from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.management.commands.preview_product_enrichment import parse_product_ids
from catalog.product_enrichment_apply import (
    ApplySnapshotError,
    apply_preview_snapshot,
)


class Command(BaseCommand):
    help = (
        'Контролируемая запись approved_fields из immutable preview JSON. '
        'По умолчанию dry-run: ничего не пишет. '
        '--apply записывает только явно approved поля. '
        'Не вызывает OpenAI и web search.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--preview-file',
            required=True,
            help='Путь к reviewed snapshot JSON, например var/reports/airfilters_20260902_v1.json',
        )
        parser.add_argument(
            '--product-ids',
            required=True,
            help='Список id через запятую. Массовый apply всех products snapshot запрещён.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Реальная запись. Без этого флага только dry-run.',
        )
        parser.add_argument(
            '--report',
            default='',
            help='Каталог для CSV/JSON отчёта apply. По умолчанию var/reports/',
        )

    def handle(self, *args, **options):
        ids = parse_product_ids(options.get('product_ids') or '')
        if not ids:
            raise CommandError('Укажите --product-ids')

        preview_file = Path(str(options.get('preview_file') or '')).expanduser()
        if not preview_file.is_file():
            raise CommandError(f'Preview-файл не найден: {preview_file}')

        report_dir = Path(options['report'] or (Path(settings.BASE_DIR) / 'var' / 'reports'))
        apply = bool(options.get('apply'))
        try:
            outcome = apply_preview_snapshot(
                preview_file=preview_file,
                product_ids=ids,
                apply=apply,
                report_dir=report_dir,
            )
        except ApplySnapshotError as exc:
            raise CommandError(str(exc)) from exc

        mode = 'apply' if apply else 'dry-run'
        self.stdout.write(f'mode {mode}')
        for item in outcome['results']:
            self.stdout.write(
                f"product {item.get('product_id')} {item.get('article') or '-'} "
                f"status={item.get('status')}"
            )
            changed = item.get('changed_fields') or []
            before = item.get('before') or {}
            after = item.get('after') or {}
            for field_name in changed:
                self.stdout.write(f'  {field_name}')
                self.stdout.write(f"    before: {before.get(field_name, '')}")
                self.stdout.write(f"    after: {after.get(field_name, '')}")
            for error in item.get('errors') or []:
                self.stdout.write(f'  error: {error}')

        summary = outcome['summary']
        self.stdout.write('SUMMARY:')
        self.stdout.write(f"total {summary['total']}")
        self.stdout.write(f"ready {summary['ready']}")
        self.stdout.write(f"changed {summary['changed']}")
        self.stdout.write(f"unchanged {summary['unchanged']}")
        self.stdout.write(f"stale {summary['stale']}")
        self.stdout.write(f"errors {summary['errors']}")
        self.stdout.write(f"report_csv {outcome['csv_path']}")
        self.stdout.write(f"report_json {outcome['json_path']}")
        if not apply:
            self.stdout.write('Dry-run: Product не изменён. Для записи укажите --apply.')
