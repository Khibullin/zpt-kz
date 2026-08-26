from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.barcode_sync import sync_barcodes_from_xlsx
from catalog.models import ProductBarcode, SellerProfile


class Command(BaseCommand):
    help = (
        'Синхронизация ProductBarcode из WMS Excel. '
        'Match только seller_profile + article. '
        'Отсутствие штрихкода в файле не удаляет старые коды. '
        '--dry-run не пишет в БД. '
        'Не заполняет ProductFulfillment.external_id из артикула.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--xlsx', required=True, help='WMS workbook со штрихкодами')
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--articles',
            default='',
            help='Список артикулов через запятую',
        )

    def handle(self, *args, **options):
        path = Path(options['xlsx'])
        if not path.exists():
            raise CommandError(f'Не найден файл: {path}')
        try:
            seller = SellerProfile.objects.get(pk=options['seller_profile_id'])
        except SellerProfile.DoesNotExist as exc:
            raise CommandError(
                f'SellerProfile id={options["seller_profile_id"]} не найден'
            ) from exc
        articles = [
            item.strip()
            for item in (options['articles'] or '').split(',')
            if item.strip()
        ] or None
        dry_run = bool(options['dry_run'])
        before = ProductBarcode.objects.count()
        results, _inspection = sync_barcodes_from_xlsx(
            xlsx_path=path,
            seller=seller,
            dry_run=dry_run,
            articles=articles,
        )
        after = ProductBarcode.objects.count()
        self.stdout.write(f"mode: {'dry-run' if dry_run else 'write'}")
        self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
        self.stdout.write(f'rows: {len(results)}')
        created = sum(1 for item in results if item.action == 'created')
        unchanged = sum(1 for item in results if item.action == 'unchanged')
        skipped = sum(1 for item in results if item.action == 'skipped')
        errors = sum(1 for item in results if item.action == 'error')
        self.stdout.write(
            f'CREATED={created} UNCHANGED={unchanged} SKIPPED={skipped} ERROR={errors}'
        )
        if dry_run and after != before:
            raise CommandError('dry-run изменил ProductBarcode — это ошибка.')
        for item in results:
            extra = ''
            if item.warnings:
                extra += ' WARN ' + ';'.join(item.warnings)
            if item.errors:
                extra += ' ERR ' + ';'.join(item.errors)
            self.stdout.write(f'{item.action.upper()}\t{item.article}{extra}')
