from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.kaspi_stock_update_preview import (
    build_preview_from_kaspi_export,
    build_preview_from_listings,
    write_kaspi_stock_preview_xlsx,
)


class Command(BaseCommand):
    help = (
        'PREVIEW_ONLY файл обновления остатков Kaspi из PP2. '
        'Ничего не пишет в Kaspi и не меняет цены.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            required=True,
            help='Путь к XLSX preview.',
        )
        parser.add_argument(
            '--use-live-pp2',
            action='store_true',
            help='Брать текущий PP2 из БД вместо встроенного снимка Rapido.',
        )
        parser.add_argument(
            '--kaspi-export',
            default='',
            help='Kaspi active.xlsx для offline preview без production listings.',
        )

    def handle(self, *args, **options):
        export_path = str(options.get('kaspi_export') or '').strip()
        if export_path:
            path = Path(export_path)
            if not path.exists():
                raise CommandError(f'Не найден файл: {path}')
            result = build_preview_from_kaspi_export(path)
        else:
            result = build_preview_from_listings(
                use_live_pp2=bool(options['use_live_pp2'])
            )
        path = write_kaspi_stock_preview_xlsx(result, Path(options['output']))
        self.stdout.write(f'file: {path}')
        self.stdout.write('upload_ready: NO')
        self.stdout.write(f'ready listings = {result.ready_count}')
        self.stdout.write(f'hold listings = {result.hold_listing_count}')
        self.stdout.write(
            f'duplicate groups = {len(result.duplicate_groups)}: '
            f'{", ".join(result.duplicate_groups)}'
        )
        self.stdout.write(f'no listing = {", ".join(result.no_listing) or "(none)"}')
        self.stdout.write('Kaspi writes = 0')
