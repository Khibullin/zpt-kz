from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import BaseCommand, CommandError

from catalog.ag_parts_import import (
    CatalogMatcher,
    ImportResult,
    attach_archive_photos,
    filter_rows,
    index_photos,
    inspect_workbook,
    load_cost_index,
    load_sheet_rows,
    merge_prepared,
    prepared_from_excel_row,
    summarize,
    unpack_archives,
    upsert_product,
    write_reports,
)
from catalog.models import SellerProfile


class Command(BaseCommand):
    help = (
        'Импорт товаров AG Parts из Excel и фотоархивов. '
        'По умолчанию только отчёт: --dry-run. '
        'Реальная запись требует явного запуска без --dry-run '
        'и --seller-profile-id.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--price-xlsx', required=True, help='Excel со списком товаров / розничными ценами')
        parser.add_argument('--cost-xlsx', default='', help='Excel с себестоимостью (необязательно)')
        parser.add_argument(
            '--photos',
            action='append',
            default=[],
            help='Каталог или zip с фотографиями (можно повторять)',
        )
        parser.add_argument('--seller-profile-id', type=int, default=None)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=None)
        parser.add_argument(
            '--articles',
            default='',
            help='Список артикулов через запятую',
        )
        parser.add_argument('--replace-images', action='store_true')
        parser.add_argument(
            '--wholesale-min-qty',
            type=int,
            default=None,
            help='Устарело: B2B tiers на этом этапе не создаются',
        )
        parser.add_argument('--report', default='', help='Путь без расширения для JSON/CSV отчёта')

    def handle(self, *args, **options):
        price_path = Path(options['price_xlsx'])
        if not price_path.exists():
            raise CommandError(f'Не найден прайс: {price_path}')

        dry_run = options['dry_run']
        seller = self._resolve_seller(options['seller_profile_id'], dry_run=dry_run)
        if seller is None and not dry_run:
            raise CommandError(
                'Реальный импорт остановлен: укажите однозначный '
                '--seller-profile-id для AG Parts.'
            )

        price_inspect = inspect_workbook(price_path)
        self._print_inspection('PRICE', price_inspect)

        headers, column_map, data_rows, images_by_row = load_sheet_rows(
            price_path,
            price_inspect.chosen_sheet,
        )
        if 'article' not in column_map:
            raise CommandError(
                'В прайсе не найдена колонка артикула. '
                f'Заголовки: {headers}'
            )

        embedded_rows = set()
        for sheet in price_inspect.sheets:
            if sheet.name == price_inspect.chosen_sheet:
                embedded_rows = set(sheet.embedded_image_rows)
        data_row_numbers = {row_number for row_number, _values in data_rows}
        embedded_usable = bool(embedded_rows) and embedded_rows.issubset(data_row_numbers)
        if sheet_embedded_count(price_inspect) and not embedded_usable:
            self.stdout.write(self.style.WARNING(
                'Embedded images Excel не сопоставлены однозначно со строками. '
                'Основной источник фото — архивы/каталоги.'
            ))
            images_by_row = {}

        prepared = {}
        for row_number, values in data_rows:
            row = prepared_from_excel_row(
                row_number,
                values,
                column_map,
                price_inspect.chosen_sheet,
                images_by_row if embedded_usable else {},
            )
            if row.article_key in prepared:
                prepared[row.article_key] = merge_prepared(
                    prepared[row.article_key],
                    row,
                )
            else:
                prepared[row.article_key] = row

        cost_inspect = None
        cost_warnings = []
        if options['cost_xlsx']:
            cost_path = Path(options['cost_xlsx'])
            if not cost_path.exists():
                raise CommandError(f'Не найден файл себестоимости: {cost_path}')
            cost_index, cost_inspect, cost_warnings = load_cost_index(cost_path)
            self._print_inspection('COST', cost_inspect)
            for row in prepared.values():
                if row.article_key in cost_index:
                    row.cost_price = cost_index[row.article_key]

        photo_roots = []
        unpack_context = TemporaryDirectory(prefix='ag-parts-photos-')
        try:
            archives = []
            directories = []
            for item in options['photos']:
                path = Path(item)
                if path.suffix.lower() == '.zip':
                    archives.append(path)
                else:
                    directories.append(path)
            if archives:
                photo_roots.extend(unpack_archives(archives, unpack_context.name))
            photo_roots.extend(directories)
            photo_index, photo_file_count = index_photos(photo_roots)
            attach_archive_photos(list(prepared.values()), photo_index)

            article_filter = [
                item.strip()
                for item in (options['articles'] or '').split(',')
                if item.strip()
            ] or None
            rows = filter_rows(
                list(prepared.values()),
                limit=options['limit'],
                articles=article_filter,
            )

            matcher = CatalogMatcher()
            results = []
            for row in rows:
                try:
                    results.append(
                        upsert_product(
                            row,
                            seller,
                            matcher,
                            dry_run=dry_run,
                            replace_images=options['replace_images'],
                            expect_cost=bool(options['cost_xlsx']),
                        )
                    )
                except Exception as exc:
                    results.append(ImportResult(
                        article=row.article,
                        action='error',
                        source_row=row.source_row,
                        warnings=list(row.warnings),
                        errors=[f'row_exception:{exc}'],
                    ))

            totals = summarize(results)
            unique_articles = len(prepared)
            with_cost = sum(1 for item in prepared.values() if item.cost_price is not None)
            with_photos = sum(1 for item in prepared.values() if item.photos)
            without_photos = [
                item.article for item in prepared.values() if not item.photos
            ]
            categories = sorted({
                item.category_raw for item in prepared.values() if item.category_raw
            })
            brands = sorted({
                item.brand_raw for item in prepared.values() if item.brand_raw
            })

            self.stdout.write('')
            self.stdout.write(self.style.NOTICE('=== AG Parts import ==='))
            self.stdout.write(f"mode: {'dry-run' if dry_run else 'write'}")
            if seller:
                self.stdout.write(f'seller_profile: {seller.pk} {seller.name}')
            self.stdout.write(f'price rows: {sum(sheet.row_count for sheet in price_inspect.sheets)}')
            self.stdout.write(f'unique articles: {unique_articles}')
            self.stdout.write(f'selected for this run: {len(rows)}')
            self.stdout.write(f'categories in source: {categories}')
            self.stdout.write(f'brand column values: {brands}')
            self.stdout.write(f'articles with cost_price: {with_cost}')
            self.stdout.write(f'articles missing cost_price: {unique_articles - with_cost}')
            self.stdout.write(f'photo files indexed: {photo_file_count}')
            self.stdout.write(f'articles with photos: {with_photos}')
            self.stdout.write(f'articles without photos: {len(without_photos)}')
            if without_photos[:30]:
                self.stdout.write('missing photo articles: ' + ', '.join(without_photos[:30]))
            qty_cols = []
            for sheet in price_inspect.sheets:
                qty_cols.extend(sheet.quantity_columns)
            self.stdout.write(f'quantity columns detected: {qty_cols or "none"}')
            self.stdout.write(
                'stock_qty: not imported (purchase qty is not current warehouse stock)'
            )
            if options['wholesale_min_qty'] is not None:
                self.stdout.write(self.style.WARNING(
                    '--wholesale-min-qty ignored: B2B tiers/sale/promo/consignment '
                    'are not created in this import stage'
                ))
            self.stdout.write(
                'retail: valid price > 0 -> Product.price, price_on_request=False; '
                'missing/invalid price -> price empty, price_on_request=True'
            )
            self.stdout.write(
                'b2b: ProductPriceTier / sale / promo / consignment are NOT created'
            )
            prices = [item.retail_price for item in prepared.values() if item.retail_price]
            if prices:
                self.stdout.write(f'retail price range: {min(prices)}-{max(prices)}')
            else:
                self.stdout.write('retail price range: none (no retail column/values)')
            missing_price = [
                item.article for item in prepared.values()
                if not item.retail_price
            ]
            self.stdout.write(f'articles without valid retail price: {len(missing_price)}')
            types = sorted({item.product_type for item in prepared.values() if item.product_type})
            self.stdout.write(f'inferred product types: {types}')
            self.stdout.write('--- 10 sample rows ---')
            for item in list(prepared.values())[:10]:
                self.stdout.write(
                    f"{item.article}\ttype={item.product_type or '-'}\t"
                    f"cat={item.category_name or '-'}\t"
                    f"brand={item.brand_raw or '-'}\t"
                    f"price={item.retail_price or '-'}\t"
                    f"photos={len(item.photos)}\t"
                    f"warn={';'.join(item.warnings) or '-'}"
                )
            if cost_warnings:
                self.stdout.write('cost warnings: ' + '; '.join(cost_warnings))

            for item in results:
                extra = ''
                if item.warnings:
                    extra += ' WARN ' + ';'.join(item.warnings)
                if item.errors:
                    extra += ' ERR ' + ';'.join(item.errors)
                self.stdout.write(
                    f'{item.action.upper()}\t{item.article}\trow={item.source_row}{extra}'
                )

            self.stdout.write('')
            self.stdout.write(
                'TOTALS  '
                f"CREATED={totals['CREATED']} "
                f"UPDATED={totals['UPDATED']} "
                f"SKIPPED={totals['SKIPPED']} "
                f"WARNING={totals['WARNING']} "
                f"ERROR={totals['ERROR']}"
            )
            if options['report']:
                write_reports(results, options['report'])
                self.stdout.write(f'report: {options["report"]}.json / .csv')
        finally:
            unpack_context.cleanup()

    def _resolve_seller(self, seller_id, *, dry_run):
        try:
            if seller_id:
                try:
                    return SellerProfile.objects.get(pk=seller_id)
                except SellerProfile.DoesNotExist as exc:
                    raise CommandError(f'SellerProfile id={seller_id} не найден') from exc
            matches = list(SellerProfile.objects.filter(name__iexact='AG Parts'))
        except Exception as exc:
            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f'SellerProfile недоступен в этой БД ({exc}). '
                    'Dry-run продолжается без upsert-классификации по существующим товарам.'
                ))
                return None
            raise CommandError(f'Не удалось прочитать SellerProfile: {exc}') from exc
        if len(matches) == 1:
            return matches[0]
        names = ', '.join(f'{item.pk}:{item.name}' for item in matches) or 'нет'
        message = (
            'Однозначный SellerProfile AG Parts не найден. '
            f'Совпадения: {names}. Укажите --seller-profile-id.'
        )
        if dry_run:
            self.stdout.write(self.style.WARNING(message))
            return None
        raise CommandError(message)

    def _print_inspection(self, label, inspection):
        self.stdout.write(self.style.NOTICE(f'--- {label} {inspection.path} ---'))
        for sheet in inspection.sheets:
            self.stdout.write(
                f"sheet={sheet.name!r} rows={sheet.row_count} "
                f"headers={sheet.headers} map={sheet.column_map} "
                f"embedded_images={sheet.embedded_image_count} "
                f"qty_cols={sheet.quantity_columns}"
            )
        self.stdout.write(f'chosen_sheet={inspection.chosen_sheet!r}')


def sheet_embedded_count(inspection):
    return sum(sheet.embedded_image_count for sheet in inspection.sheets)
