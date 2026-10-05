"""Print the SellerLead daily summary. Does not send it anywhere."""

from django.core.management.base import BaseCommand

from core.services.seller_lead_daily_report import build_seller_lead_daily_report


class Command(BaseCommand):
    help = 'Печатает операционную сводку SellerLead. Ничего не отправляет.'

    def handle(self, *args, **options):
        self.stdout.write(build_seller_lead_daily_report())
