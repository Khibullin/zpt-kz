from django.core.management.base import BaseCommand

from core.vehicle_catalog_sync import sync_vehicle_catalog


class Command(BaseCommand):
    help = (
        'Загрузка справочников стран, марок и моделей для ZPT Market. '
        'Данные берутся из core/vehicle_catalog.py (единый источник).'
    )

    def handle(self, *args, **options):
        stats = sync_vehicle_catalog(transport_type='car')['catalog']
        self.stdout.write(self.style.SUCCESS(
            f"Catalog: стран={stats['countries']}, "
            f"марок={stats['brands']}, "
            f"моделей={stats['models']}"
        ))
