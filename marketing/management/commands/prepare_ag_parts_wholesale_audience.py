from django.core.management.base import BaseCommand, CommandError

from marketing.services.campaigns.ag_parts_wholesale_report import (
    build_ag_parts_wholesale_audience_report,
)


class Command(BaseCommand):
    help = (
        'Recalculate AG Parts wholesale audience and optionally prepare a campaign '
        'snapshot. Never sends WhatsApp messages.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--prepare',
            action='store_true',
            help='Write campaign recipient snapshot after calculating the audience.',
        )

    def handle(self, *args, **options):
        try:
            report = build_ag_parts_wholesale_audience_report(
                prepare=bool(options.get('prepare')),
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        for key, value in report.items():
            self.stdout.write(f'{key}: {value}')
        self.stdout.write(self.style.WARNING('Messages were not sent.'))
