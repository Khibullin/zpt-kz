from django.core.management.base import BaseCommand, CommandError

from core.services.seller_lead_contact_search import enrich_seller_lead_contacts
from core.services.seller_lead_search import (
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    get_seller_search_settings,
)


class Command(BaseCommand):
    help = 'Ищет публичные WhatsApp-контакты для SellerLead через Brave Search API.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default=None, help='Instagram username лида')
        parser.add_argument('--limit', type=int, default=None, help='Максимум лидов для обработки')
        parser.add_argument(
            '--max-queries-per-lead',
            type=int,
            default=3,
            help='Максимум поисковых запросов на один SellerLead',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать найденные контакты без сохранения в базу',
        )

    def handle(self, *args, **options):
        settings_data = get_seller_search_settings()
        dry_run = options['dry_run']

        if not settings_data['enabled']:
            raise CommandError(
                'SELLER_SEARCH_ENABLED=False. Установите SELLER_SEARCH_ENABLED=True для реальных запросов.',
            )
        if not settings_data['api_key']:
            raise CommandError(
                'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
            )
        if settings_data['provider'] != 'brave':
            raise CommandError(
                f"Неподдерживаемый SELLER_SEARCH_PROVIDER: {settings_data['provider']}",
            )

        try:
            stats = enrich_seller_lead_contacts(
                username=options['username'],
                limit=options['limit'],
                max_queries_per_lead=max(1, options['max_queries_per_lead']),
                dry_run=dry_run,
                search_settings=settings_data,
            )
        except SellerLeadSearchConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f'Обработано лидов: {stats.leads_processed}')
        self.stdout.write(f'Выполнено поисковых запросов: {stats.queries_executed}')
        self.stdout.write(f'Найдено кандидатов: {stats.candidates_found}')
        self.stdout.write(f'High confidence: {stats.high_confidence}')
        self.stdout.write(f'Medium confidence: {stats.medium_confidence}')
        self.stdout.write(f'Low confidence: {stats.low_confidence}')
        self.stdout.write(f'Конфликтов: {stats.conflicts}')
        self.stdout.write(f'Готово к сохранению: {stats.ready_to_save}')
        self.stdout.write(f'Сохранено: {stats.saved}')
        self.stdout.write(f'Ошибок: {stats.errors}')

        if stats.lead_outcomes:
            self.stdout.write('Результаты по лидам:')
            for outcome in stats.lead_outcomes:
                status_label = 'сохранён' if outcome.accepted and not dry_run else (
                    'готов к сохранению' if outcome.accepted else 'отклонён'
                )
                phone_label = outcome.phone or '(нет)'
                confidence_label = outcome.confidence or '(нет)'
                self.stdout.write(
                    f"  @{outcome.username} | {phone_label} | {confidence_label} | "
                    f"{outcome.source_url or '(нет URL)'} | {status_label} | {outcome.rejection_reason}",
                )
                if outcome.source_text:
                    self.stdout.write(f"    source_text: {outcome.source_text[:200]}")

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS('Обогащение WhatsApp-контактов завершено.'))
