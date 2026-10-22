"""Read-only report of missing source facts behind Google Product Offer warnings.

Run only against a database whose access is explicitly authorized. This command
never edits catalog entries, seller terms, prices, or availability.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from catalog.models import Product
from catalog.templatetags.product_extras import public_product_url


class Command(BaseCommand):
    help = 'Report priced active products with unknown stock or missing specific category (read-only).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=25,
            help='Maximum number of affected public product paths to display (default 25).',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        if limit < 0:
            raise CommandError('--limit must be zero or greater')

        offers = Product.objects.filter(
            status='active',
            price__isnull=False,
            price_on_request=False,
        )
        unknown_stock = offers.filter(stock_qty__isnull=True)
        missing_specific_category = offers.filter(category__isnull=True)

        self.stdout.write(f'priced_active_products={offers.count()}')
        self.stdout.write(f'unknown_stock_count={unknown_stock.count()}')
        self.stdout.write(f'missing_specific_category_count={missing_specific_category.count()}')
        self.stdout.write(
            'Unknown stock must not be marked InStock, OutOfStock, or PreOrder '
            'until the seller confirms its actual status.'
        )

        affected = (
            offers.filter(Q(stock_qty__isnull=True) | Q(category__isnull=True))
            .order_by('pk')
            .select_related('category')[:limit]
        )
        for product in affected:
            issues = []
            if product.stock_qty is None:
                issues.append('stock=unknown')
            if product.category_id is None:
                issues.append('specific_category=missing')
            self.stdout.write(
                f'product_id={product.pk}\t{public_product_url(product)}\t{",".join(issues)}'
            )
