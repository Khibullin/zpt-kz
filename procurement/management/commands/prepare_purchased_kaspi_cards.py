from django.core.management.base import BaseCommand
from procurement.kaspi_materials import process_purchased_cards


class Command(BaseCommand):
    help = 'Prepare existing purchased Kaspi cards without changing stock or publishing.'
    def add_arguments(self, parser):
        parser.add_argument('--photos', action='store_true')
    def handle(self, *args, **options):
        count = process_purchased_cards(fetch_photos=options['photos'])
        self.stdout.write(f'Purchased Kaspi positions checked: {count}')
