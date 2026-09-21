from django.core.management.base import BaseCommand

from catalog.maintenance_kit_seed import (
    apply_maintenance_kits,
    format_plan_report,
    plan_maintenance_kits,
)


class Command(BaseCommand):
    help = (
        'Создаёт первые комплекты ТО по точным артикулам. '
        'По умолчанию dry-run. Запись только с --apply. '
        'Привязывает Product только если article найден ровно один раз.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Создать или обновить комплекты. Без флага — только план.',
        )

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        plans = plan_maintenance_kits()
        if apply:
            apply_maintenance_kits(plans)
        self.stdout.write(format_plan_report(plans, apply=apply))
