from django.core.management.base import BaseCommand
from core.editorial_candidates import collect_product_candidates


class Command(BaseCommand):
    help = 'Preview suggested editorial topics; use --save to store unpublished candidates.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)
        parser.add_argument('--save', action='store_true')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        items = collect_product_candidates(limit=limit, create=options['save'])
        for item in items:
            self.stdout.write(f"{item['source_key']}: {item['title']}")
        self.stdout.write(f"Total: {len(items)}; saved={options['save']}")
