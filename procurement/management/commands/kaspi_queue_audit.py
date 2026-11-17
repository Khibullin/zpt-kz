"""Read-only operational report: actionable Kaspi tasks versus missing identity mapping."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from procurement.models import KaspiReviewTask, KaspiSellerSnapshot, Offer


class Command(BaseCommand):
    help = 'Report Kaspi price freshness and review queue without contacting Kaspi or changing data.'

    def handle(self, *args, **options):
        today = timezone.localdate()
        tasks = KaspiReviewTask.objects.all()
        total = tasks.count()
        pending = tasks.filter(status='pending')
        actionable = pending.filter(
            offer__match_verified=True,
        ).exclude(offer__kaspi_url='').filter(
            Q(batch__isnull=True) | Q(batch__status='done')
        )
        identity = pending.filter(offer__match_verified=False).exclude(offer__kaspi_url='')
        missing = pending.filter(offer__kaspi_url='')
        complete = KaspiSellerSnapshot.objects.filter(complete=True)
        stats = {
            'total_tasks': total,
            'pending_total': pending.count(),
            'pending_without_kaspi_url': missing.count(),
            'pending_card_identity_check': identity.count(),
            'pending_verified_price_refresh': actionable.count(),
            'blocked': tasks.filter(status='blocked').count(),
            'done': tasks.filter(status='done').count(),
            'complete_snapshots': complete.count(),
            'complete_snapshots_last_7_days': complete.filter(
                checked_on__gte=today-timedelta(days=7), checked_on__lte=today
            ).count(),
            'verified_offers_with_recent_kaspi_price': Offer.objects.filter(
                match_verified=True, kaspi_price_checked_on__gte=today-timedelta(days=7),
                kaspi_price_checked_on__lte=today,
            ).count(),
        }
        for key, value in stats.items():
            self.stdout.write(f'{key}: {value}')
        self.stdout.write('Blocked reasons (top 10):')
        for row in tasks.filter(status='blocked').values('reason').annotate(
            total=Count('id')
        ).order_by('-total')[:10]:
            self.stdout.write(f"  {row['total']}: {row['reason']}")
