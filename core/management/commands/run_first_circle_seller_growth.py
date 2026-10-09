from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.kazakhstan_locations import (
    FIRST_CIRCLE_CITIES,
    FIRST_CIRCLE_SELLER_SATURATION_TARGET,
    canonical_kazakhstan_city,
)
from core.services.seller_growth import (
    DEFAULT_DAILY_TARGET_PER_CITY,
    grow_first_circle_city,
)
from core.services.seller_lead_pipeline_guard import PipelineLockBusy, PipelineRunLock


class Command(BaseCommand):
    help = (
        'Daily bounded Seller Growth for the 19 first-circle Kazakhstan cities. '
        'Saturated cities are skipped; up to three verified sellers per city '
        'are activated for buyer requests without marketing consent.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--city',
            action='append',
            default=[],
            help='Optional first-circle city. Repeat to limit a manual run.',
        )
        parser.add_argument(
            '--target-per-city',
            type=int,
            default=DEFAULT_DAILY_TARGET_PER_CITY,
        )
        parser.add_argument(
            '--saturation-threshold',
            type=int,
            default=FIRST_CIRCLE_SELLER_SATURATION_TARGET,
        )
        parser.add_argument(
            '--candidate-limit',
            type=int,
            default=12,
        )
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        target = int(options['target_per_city'])
        saturation = int(options['saturation_threshold'])
        candidate_limit = int(options['candidate_limit'])
        if target < 1 or target > 10:
            raise CommandError('target-per-city должен быть от 1 до 10.')
        if saturation < 1:
            raise CommandError('saturation-threshold должен быть больше 0.')
        if candidate_limit < target or candidate_limit > 50:
            raise CommandError('candidate-limit должен быть от target-per-city до 50.')

        requested = options['city'] or list(FIRST_CIRCLE_CITIES)
        cities: list[str] = []
        for raw in requested:
            city = canonical_kazakhstan_city(raw)
            if city not in FIRST_CIRCLE_CITIES:
                raise CommandError(f'{raw} не входит в первый круг Seller Growth.')
            if city not in cities:
                cities.append(city)

        day_index = timezone.localdate().toordinal()
        totals = {
            'cities': 0,
            'saturated': 0,
            'activated': 0,
            'created': 0,
            'updated': 0,
            'hits': 0,
            'enriched': 0,
            'errors': 0,
        }

        try:
            with PipelineRunLock():
                for city in cities:
                    result = grow_first_circle_city(
                        city,
                        day_index=day_index,
                        target=target,
                        saturation_threshold=saturation,
                        candidate_limit=candidate_limit,
                        dry_run=bool(options['dry_run']),
                    )
                    totals['cities'] += 1
                    totals['saturated'] += int(result.saturated)
                    totals['activated'] += result.activated
                    totals['created'] += result.discovery_created
                    totals['updated'] += result.discovery_updated
                    totals['hits'] += result.discovery_hits
                    totals['enriched'] += result.enriched
                    totals['errors'] += len(result.errors) + result.discovery_errors

                    state = 'SATURATED' if result.saturated else 'OK'
                    self.stdout.write(
                        f'{city}: {state} active={result.active_before}->{result.active_after} '
                        f'activated={result.activated}/{target} '
                        f'discovery(created={result.discovery_created}, '
                        f'updated={result.discovery_updated}, hits={result.discovery_hits}) '
                        f'enriched={result.enriched} skipped={result.skipped_candidates}'
                    )
                    for error in result.errors:
                        self.stdout.write(self.style.WARNING(f'  {error}'))
        except PipelineLockBusy:
            self.stdout.write(self.style.WARNING(
                'Seller Growth уже выполняется другим процессом. Запуск пропущен.'
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            'FIRST CIRCLE SELLER GROWTH: '
            f"cities={totals['cities']} saturated={totals['saturated']} "
            f"activated={totals['activated']} created={totals['created']} "
            f"updated={totals['updated']} hits={totals['hits']} "
            f"enriched={totals['enriched']} errors={totals['errors']}"
        ))
