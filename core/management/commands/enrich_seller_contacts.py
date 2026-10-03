"""Enrich SellerLead contacts from allowed public sources.

Not wired to cron. Does not send WhatsApp, email, or SMS, and does not create
Seller, User, SellerProfile, or Product rows. Kolesa is not called.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import SellerLead
from core.services.seller_contact_enrichment import (
    ALLOWED_SOURCES,
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)
from core.services.seller_lead_contact_search import normalize_kz_whatsapp_phone

MAX_LIMIT = 5


def _whatsapp_state(result) -> str:
    if result.conflicts:
        return 'CONFLICT'
    if result.verified_whatsapp:
        return 'VERIFIED'
    if result.pending_candidates:
        return 'PENDING'
    return 'NOT FOUND'


class Command(BaseCommand):
    help = (
        'Ищет публичные контакты SellerLead в разрешённых источниках. '
        'Не отправляет сообщения и не создаёт продавцов.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--lead-id', action='append', dest='lead_ids', type=int, default=[])
        parser.add_argument('--city', default='')
        parser.add_argument('--limit', type=int, default=1)
        parser.add_argument('--source', action='append', dest='sources', default=[])
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument(
            '--stop-on-verified-whatsapp',
            action='store_true',
            help=(
                'Пропустить остальные источники, если официальный сайт уже дал явный WhatsApp. '
                'Без этого флага проверяются все выбранные источники.'
            ),
        )

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        apply = bool(options['apply'])
        if dry_run and apply:
            raise CommandError('Укажите только один режим: --dry-run или --apply.')
        if not dry_run and not apply:
            raise CommandError('Укажите --dry-run или --apply. Без режима сеть и запись не выполняются.')

        aliases = {'2gis': 'two_gis', 'yandex': 'yandex_org'}
        sources = [
            aliases.get(str(item).strip().lower().replace('-', '_'), str(item).strip().lower().replace('-', '_'))
            for item in options['sources']
        ]
        if not sources:
            raise CommandError(
                'Укажите --source website, google_places, brave, two_gis, yandex_org или all.',
            )
        unknown = [item for item in sources if item not in ALLOWED_SOURCES and item != 'kolesa']
        if unknown:
            raise CommandError(f'Неизвестный источник enrichment: {unknown[0]}')

        limit = options['limit']
        if limit < 1 or limit > MAX_LIMIT:
            raise CommandError(f'--limit должен быть от 1 до {MAX_LIMIT}.')

        leads = list(self._leads(options['lead_ids'], options['city'], limit))
        if not leads:
            raise CommandError('SellerLead для обогащения не найден.')

        for lead in leads:
            try:
                result = enrich_seller_lead_contacts(
                    lead,
                    sources=sources,
                    dry_run=dry_run,
                    stop_on_verified_whatsapp=bool(options['stop_on_verified_whatsapp']),
                )
            except SellerContactEnrichmentError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                f'#{lead.pk} {lead.name} | {result.outcome} | '
                f'contacts={len(result.observations)} | dry_run={result.dry_run} | '
                f'whatsapp_state={_whatsapp_state(result)}'
            )
            for run in result.source_runs:
                detail = f' {run.detail}' if run.detail else ''
                self.stdout.write(f'  source {run.source}: {run.status}{detail}')
            for hit in result.locators:
                detail = f' {hit.detail}' if hit.detail else ''
                self.stdout.write(f'  locator {hit.source}: {hit.website_url}{detail}')
            for site in result.websites_discovered:
                self.stdout.write(f'  website_discovered {site}')
            for site in result.websites_considered:
                self.stdout.write(f'  website_crawled {site}')
            for site in result.websites_skipped_brave_cap:
                self.stdout.write(f'  website_skipped_brave_cap {site}')
            for site in result.websites_skipped_blocked:
                self.stdout.write(f'  website_skipped_blocked {site}')
            for site in result.websites_skipped_budget:
                self.stdout.write(f'  website_skipped_budget {site}')
            verified_numbers = set(result.verified_whatsapp)
            for observation in result.observations:
                source = _observation_source_label(observation, result.two_gis_external_id)
                note = ''
                phone = normalize_kz_whatsapp_phone(observation.value) or ''
                if observation.field_name == 'phone' and phone and phone in verified_numbers:
                    note = ' (same as verified WhatsApp)'
                self.stdout.write(
                    f'  [{observation.field_name}] {observation.value} '
                    f'confidence={observation.confidence} explicit={observation.explicit_whatsapp} '
                    f'origin={observation.origin} source={source}{note}'
                )
            if result.verified_whatsapp:
                self.stdout.write(f'  verified_whatsapp={",".join(result.verified_whatsapp)}')
            if result.pending_candidates:
                self.stdout.write(f'  pending_whatsapp={",".join(result.pending_candidates)}')
            if result.conflicts:
                self.stdout.write(f'  conflict_whatsapp={",".join(result.conflicts)}')
            for message in result.errors:
                self.stdout.write(self.style.WARNING(message))
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Контакты обновлены. Продавцы, пользователи и товары не создавались. '
                'Сообщения не отправлялись.',
            ))

    def _leads(self, lead_ids: list[int], city: str, limit: int):
        if lead_ids:
            return SellerLead.objects.filter(pk__in=lead_ids).order_by('pk')
        if city:
            return SellerLead.objects.filter(city=city).order_by('pk')[:limit]
        raise CommandError('Укажите --lead-id или --city.')


def _observation_source_label(observation, two_gis_external_id: str) -> str:
    if observation.source_url:
        return observation.source_url
    if observation.origin == 'two_gis':
        return f'2gis:{two_gis_external_id}' if two_gis_external_id else '2gis:'
    return observation.origin or ''
