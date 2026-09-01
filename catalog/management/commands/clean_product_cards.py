from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product
from catalog.product_quality import (
    PUBLIC_TEXT_FIELDS,
    STATUS_CRITICAL,
    apply_safe_fixes,
    audit_all_products,
    summarize_audit,
    write_audit_reports,
)


class Command(BaseCommand):
    help = (
        'Безопасная очистка публичных полей карточек. '
        'По умолчанию dry-run: ничего не пишет. '
        '--apply-safe меняет только whitelist-поля '
        '(trim, служебные предложения при безопасном leftover, OEM из article). '
        'Не меняет brand/category/model/цены/ownership/status/stock.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False)
        parser.add_argument('--apply-safe', action='store_true', default=False)
        parser.add_argument('--seller', default='')
        parser.add_argument('--product-id', type=int, default=0)
        parser.add_argument('--report', default='')

    def handle(self, *args, **options):
        apply_safe = bool(options.get('apply_safe'))
        dry_run = bool(options.get('dry_run')) or not apply_safe
        if apply_safe and options.get('dry_run'):
            dry_run = True
            apply_safe = False

        qs = Product.objects.all().select_related(
            'brand',
            'car_model',
            'category',
            'seller_profile',
        ).prefetch_related('selected_brands', 'selected_models')
        seller = (options.get('seller') or '').strip()
        product_id = int(options.get('product_id') or 0)
        if product_id:
            qs = qs.filter(pk=product_id)
            if not qs.exists():
                raise CommandError(f'Product id={product_id} не найден')
        if seller:
            qs = qs.filter(seller_name__iexact=seller) | qs.filter(
                seller_profile__name__iexact=seller
            )
            qs = qs.distinct()

        products = list(qs)
        snapshots = {
            product.pk: {
                'price': product.price,
                'status': product.status,
                'seller_profile_id': product.seller_profile_id,
                'brand_id': product.brand_id,
                'category_id': product.category_id,
                'car_model_id': product.car_model_id,
                'stock_qty': product.stock_qty,
            }
            for product in products
        }
        results = audit_all_products(qs)
        summary = summarize_audit(results)
        changed_count = 0
        if apply_safe and not dry_run:
            with transaction.atomic():
                by_id = {item.pk: item for item in products}
                for item in results:
                    product = by_id.get(item.product_id)
                    if product is None or not item.safe_fixes:
                        continue
                    if item.status == STATUS_CRITICAL:
                        continue
                    apply_safe_fixes(product, item.safe_fixes)
                    changed_count += 1

        report_dir = Path(options['report'] or (Path(settings.BASE_DIR) / 'var' / 'reports'))
        csv_path, json_path = write_audit_reports(
            results,
            report_dir=report_dir,
            stem='product_card_cleanup',
        )

        mode = 'dry-run' if dry_run else 'apply-safe'
        self.stdout.write(f'mode {mode}')
        self.stdout.write('SUMMARY:')
        self.stdout.write(f"total {summary['total']}")
        self.stdout.write(f"ok {summary['ok']}")
        self.stdout.write(f"auto_fixable {summary['auto_fixable']}")
        self.stdout.write(f"manual_review {summary['manual_review']}")
        self.stdout.write(f"critical {summary['critical']}")
        self.stdout.write(f'changed {changed_count}')
        self.stdout.write(f'report_csv {csv_path}')
        self.stdout.write(f'report_json {json_path}')
        self.stdout.write('safe_fields ' + ','.join(PUBLIC_TEXT_FIELDS))

        for product in Product.objects.filter(pk__in=snapshots):
            before = snapshots[product.pk]
            if (
                product.price != before['price']
                or product.status != before['status']
                or product.seller_profile_id != before['seller_profile_id']
                or product.brand_id != before['brand_id']
                or product.category_id != before['category_id']
                or product.car_model_id != before['car_model_id']
                or product.stock_qty != before['stock_qty']
            ):
                raise CommandError('Команда изменила защищённые поля Product — это ошибка.')
