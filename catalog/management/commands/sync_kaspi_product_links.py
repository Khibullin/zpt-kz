from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.kaspi_listing_sync import (
    PROBLEM_STATUSES,
    summarize_kaspi_link_rows,
    sync_kaspi_product_links,
    write_kaspi_link_report,
)
from catalog.models import ProductBarcode, ProductKaspiListing, SellerProfile


class Command(BaseCommand):
    help = (
        'Сопоставление Product ↔ ProductKaspiListing из XLSX/CSV. '
        'Match только seller_profile + article. '
        'По умолчанию dry-run: ничего не пишет в БД. '
        'Запись только с явным --apply и только для '
        'WOULD_CREATE_LISTING / WOULD_UPDATE_LISTING. '
        'Не создаёт Product, не удаляет listing/barcode, '
        'не меняет цены, остатки и publish_to_kaspi.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'file',
            help='XLSX (обязательный формат) или CSV с колонками артикула и Kaspi ID',
        )
        parser.add_argument('--seller-profile-id', type=int, required=True)
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать безопасные строки. Без флага команда только отчёт.',
        )
        parser.add_argument(
            '--map-sku',
            choices=['article', 'master_sku'],
            default=None,
            help=(
                'Явная роль колонки SKU, только если смысл неоднозначен. '
                'По умолчанию SKU не считается артикулом.'
            ),
        )
        parser.add_argument(
            '--report',
            default='',
            help='Путь к CSV или XLSX отчёту (row_number, article, status, …)',
        )

    def handle(self, *args, **options):
        path = Path(options['file'])
        if not path.exists():
            raise CommandError(f'Не найден файл: {path}')
        try:
            seller = SellerProfile.objects.get(pk=options['seller_profile_id'])
        except SellerProfile.DoesNotExist as exc:
            raise CommandError(
                f'SellerProfile id={options["seller_profile_id"]} не найден'
            ) from exc

        apply = bool(options['apply'])
        listings_before = ProductKaspiListing.objects.count()
        barcodes_before = ProductBarcode.objects.count()
        try:
            result = sync_kaspi_product_links(
                path=path,
                seller=seller,
                apply=apply,
                map_sku=options.get('map_sku') or '',
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        listings_after = ProductKaspiListing.objects.count()
        barcodes_after = ProductBarcode.objects.count()
        if not apply and (
            listings_after != listings_before or barcodes_after != barcodes_before
        ):
            raise CommandError('dry-run изменил ProductKaspiListing или ProductBarcode.')

        counts = summarize_kaspi_link_rows(result.rows)
        mode = 'apply' if apply else 'dry-run'
        self.stdout.write(f'mode: {mode}')
        self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
        self.stdout.write(f'sheet: {result.sheet_name}')
        self.stdout.write(f'Total rows: {counts["total_rows"]}')
        self.stdout.write(f'Valid rows: {counts["valid_rows"]}')
        self.stdout.write(f'Matched: {counts["matched"]}')
        self.stdout.write(f'Would create listings: {counts["would_create_listings"]}')
        self.stdout.write(f'Would update listings: {counts["would_update_listings"]}')
        self.stdout.write(f'Missing products: {counts["missing_products"]}')
        self.stdout.write(f'Missing Kaspi IDs: {counts["missing_kaspi_ids"]}')
        self.stdout.write(f'Duplicate input articles: {counts["duplicate_input_articles"]}')
        self.stdout.write(f'Master SKU conflicts: {counts["master_sku_conflicts"]}')
        self.stdout.write(f'Product conflicts: {counts["product_conflicts"]}')
        self.stdout.write(f'Merchant SKU conflicts: {counts["merchant_sku_conflicts"]}')
        self.stdout.write(f'Barcode conflicts: {counts["barcode_conflicts"]}')
        self.stdout.write(f'Multiple master SKUs: {counts["multiple_master_skus"]}')
        self.stdout.write(f'Invalid rows: {counts["invalid_rows"]}')

        problem_rows = [
            row for row in result.rows
            if row.status in PROBLEM_STATUSES or row.status != 'MATCHED'
        ]
        if problem_rows:
            self.stdout.write('')
            self.stdout.write('Non-matched rows:')
            for row in problem_rows:
                self.stdout.write(
                    f'row={row.row_number}\t'
                    f'article={row.article}\t'
                    f'master_sku={row.master_sku}\t'
                    f'merchant_sku={row.merchant_sku}\t'
                    f'barcode={row.barcode}\t'
                    f'status={row.status}\t'
                    f'reason={row.reason}'
                )

        report = (options['report'] or '').strip()
        if report:
            saved = write_kaspi_link_report(result.rows, Path(report))
            self.stdout.write(f'report: {saved}')
