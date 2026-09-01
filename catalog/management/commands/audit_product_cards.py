from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product
from catalog.product_quality import (
    audit_all_products,
    summarize_audit,
    write_audit_reports,
)


class Command(BaseCommand):
    help = (
        'Аудит публичных полей карточек товаров. '
        'Ничего не записывает в Product. '
        'Пишет CSV/JSON отчёт в var/reports/ по умолчанию.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--seller', default='', help='Имя продавца (seller_name или SellerProfile.name)')
        parser.add_argument('--product-id', type=int, default=0)
        parser.add_argument(
            '--report',
            default='',
            help='Каталог для CSV/JSON. По умолчанию var/reports/',
        )

    def handle(self, *args, **options):
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

        results = audit_all_products(qs)
        summary = summarize_audit(results)
        report_dir = Path(options['report'] or (Path(settings.BASE_DIR) / 'var' / 'reports'))
        stem = 'product_card_audit'
        if seller:
            stem += '_' + ''.join(ch if ch.isalnum() else '_' for ch in seller)[:40]
        csv_path, json_path = write_audit_reports(results, report_dir=report_dir, stem=stem)

        self.stdout.write('SUMMARY:')
        self.stdout.write(f"total {summary['total']}")
        self.stdout.write(f"ok {summary['ok']}")
        self.stdout.write(f"auto_fixable {summary['auto_fixable']}")
        self.stdout.write(f"manual_review {summary['manual_review']}")
        self.stdout.write(f"critical {summary['critical']}")
        if summary['critical_ids']:
            ids = ', '.join(str(item) for item in summary['critical_ids'])
            self.stdout.write(f'critical_ids {ids}')
        self.stdout.write(f'report_csv {csv_path}')
        self.stdout.write(f'report_json {json_path}')
