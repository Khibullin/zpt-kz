from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import RequestDispatch, Match
from core.views import send_whatsapp_template


class Command(BaseCommand):
    help = 'Отправка волн заявок продавцам'

    def handle(self, *args, **kwargs):
        now = timezone.now()

        due_dispatches = RequestDispatch.objects.filter(
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at__lte=now
        ).select_related(
            'request',
            'seller'
        ).order_by(
            'scheduled_at',
            'position_number'
        )

        total_sent = 0

        for dispatch in due_dispatches:
            req = dispatch.request
            seller = dispatch.seller

            try:
                match, _ = Match.objects.get_or_create(
                    request=req,
                    seller=seller,
                    defaults={
                        'status': 'prepared'
                    }
                )

                wa_result = send_whatsapp_template(
                    seller.whatsapp,
                    req,
                    seller.name
                )

                if wa_result.get('ok'):
                    match.status = 'sent'
                    dispatch.status = RequestDispatch.STATUS_SENT
                    dispatch.sent_at = now

                    dispatch.save(
                        update_fields=[
                            'status',
                            'sent_at'
                        ]
                    )

                    match.save(
                        update_fields=[
                            'status'
                        ]
                    )

                    total_sent += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f'SENT: {seller.name}'
                        )
                    )

                else:
                    match.status = 'error'
                    match.save(
                        update_fields=[
                            'status'
                        ]
                    )

                    self.stdout.write(
                        self.style.ERROR(
                            f'ERROR: {seller.name}'
                        )
                    )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'FAILED {seller.name}: {str(e)}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Finished. Sent: {total_sent}'
            )
        )