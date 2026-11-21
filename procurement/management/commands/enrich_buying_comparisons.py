"""Explicit batch runs for profitable comparison sources.

Run manually, 50 records per invocation, in descending current net ROI order.
Never called by HTTP GET or app startup.
"""
from django.core.management.base import BaseCommand
from procurement.comparison_enrichment import batch


class Command(BaseCommand):
    help = 'Enrich 50 highest-ranked profitable product comparisons from exact supplier and Kaspi cards'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--apify-source', default='')
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **opts):
        limit = opts['limit']
        if not 1 <= limit <= 50:
            raise ValueError('Limit must be between 1 and 50.')
        result = batch(limit=limit, force=opts['force'],
                       source_filename=opts['apify_source'])
        self.stdout.write(self.style.SUCCESS(str(result)))
