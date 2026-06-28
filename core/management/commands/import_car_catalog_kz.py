from django.core.management.base import BaseCommand

from core.vehicle_catalog_sync import sync_vehicle_catalog


class Command(BaseCommand):
    help = (
        'Импорт справочника стран, марок и моделей в core и catalog '
        '(единый источник: core/vehicle_catalog.py)'
    )

    def handle(self, *args, **options):
        stats = sync_vehicle_catalog(transport_type='car')

        core = stats['core']
        catalog = stats['catalog']

        self.stdout.write(self.style.SUCCESS(
            'Core: '
            f"стран={core['countries']}, "
            f"марок={core['brands']}, "
            f"моделей={core['models']}"
        ))
        self.stdout.write(self.style.SUCCESS(
            'Catalog (Market): '
            f"стран={catalog['countries']}, "
            f"марок={catalog['brands']}, "
            f"моделей={catalog['models']}"
        ))
