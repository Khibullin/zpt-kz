from django.core.management.base import BaseCommand, CommandError

from catalog.ag_parts_fitment_audit import (
    FitmentAuditError,
    apply_fitment_plans,
    format_plan_report,
    load_batch,
    plan_fitment_batch,
)


class Command(BaseCommand):
    help = (
        'Правки применяемости карточек AG Parts по проверенной партии. '
        'По умолчанию dry-run. Запись только с --apply. '
        'Не меняет цены, остатки, фото, PP1/PP2, slug и артикул.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch',
            default='01',
            help='Номер партии (файл catalog/data/ag_parts_fitment_audit/batch_XX.json).',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать поля. Без флага только план.',
        )

    def handle(self, *args, **options):
        apply = bool(options['apply'])
        batch_id = str(options.get('batch') or '01')
        try:
            spec = load_batch(batch_id)
            plans = plan_fitment_batch(spec)
            apply_fitment_plans(plans, apply=apply)
        except FitmentAuditError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(format_plan_report(plans, apply=apply))
        if not apply:
            self.stdout.write('Dry-run: Product не изменён. Для записи укажите --apply.')
