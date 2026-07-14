from django.core.management.base import BaseCommand, CommandError

from core.services.seller_lead_pipeline import (
    DEFAULT_CATEGORY,
    DEFAULT_CITY,
    DEFAULT_LEAD_LIMIT,
    DEFAULT_MAX_QUERIES_PER_LEAD,
    DEFAULT_SEARCH_LIMIT,
    SellerLeadPipelineConfigError,
    run_seller_lead_pipeline,
)
from core.services.seller_lead_search import (
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    get_seller_search_settings,
)


class Command(BaseCommand):
    help = (
        'Ограниченный pipeline: поиск новых Instagram-профилей и WhatsApp '
        'только для лидов, созданных в текущем запуске.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--city', type=str, default=DEFAULT_CITY, help='Город поиска')
        parser.add_argument('--category', type=str, default=DEFAULT_CATEGORY, help='Категория поиска')
        parser.add_argument(
            '--search-limit',
            type=int,
            default=DEFAULT_SEARCH_LIMIT,
            help='Максимум результатов Brave на один поисковый запрос',
        )
        parser.add_argument(
            '--lead-limit',
            type=int,
            default=DEFAULT_LEAD_LIMIT,
            help='Максимум новых лидов для создания и обогащения',
        )
        parser.add_argument(
            '--max-queries-per-lead',
            type=int,
            default=DEFAULT_MAX_QUERIES_PER_LEAD,
            help='Максимум поисковых запросов WhatsApp на один лид',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Полная проверка pipeline без записи в базу',
        )
        parser.add_argument(
            '--skip-discovery',
            action='store_true',
            help='Пропустить этап поиска Instagram-профилей',
        )
        parser.add_argument(
            '--skip-enrichment',
            action='store_true',
            help='Пропустить этап поиска WhatsApp',
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
            stats = run_seller_lead_pipeline(
                city=options['city'],
                category=options['category'],
                search_limit=options['search_limit'],
                lead_limit=options['lead_limit'],
                max_queries_per_lead=options['max_queries_per_lead'],
                dry_run=dry_run,
                skip_discovery=options['skip_discovery'],
                skip_enrichment=options['skip_enrichment'],
                search_settings=settings_data,
            )
        except SellerLeadPipelineConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchError as exc:
            raise CommandError(str(exc)) from exc

        discovery = stats.discovery
        enrichment = stats.enrichment

        self.stdout.write('DISCOVERY:')
        if discovery.skipped:
            self.stdout.write('  пропущен (--skip-discovery)')
        else:
            self.stdout.write(f'  поисковых запросов: {discovery.queries_executed}')
            self.stdout.write(f'  результатов Brave: {discovery.results_found}')
            self.stdout.write(f'  распознано профилей: {discovery.profiles_parsed}')
            self.stdout.write(f'  новых профилей: {discovery.new_profiles}')
            self.stdout.write(f'  пропущено дублей: {discovery.duplicates_skipped}')
            self.stdout.write(f'  отклонено ссылок: {discovery.links_rejected}')
            self.stdout.write(f'  ошибок: {discovery.errors}')

        self.stdout.write('ENRICHMENT:')
        if enrichment.skipped:
            self.stdout.write('  пропущен (--skip-enrichment)')
        else:
            self.stdout.write(f'  обработано новых лидов: {enrichment.leads_processed}')
            self.stdout.write(f'  поисковых запросов: {enrichment.queries_executed}')
            self.stdout.write(f'  найдено номеров: {enrichment.candidates_found}')
            self.stdout.write(f'  high: {enrichment.high_confidence}')
            self.stdout.write(f'  medium: {enrichment.medium_confidence}')
            self.stdout.write(f'  low: {enrichment.low_confidence}')
            self.stdout.write(f'  конфликтов: {enrichment.conflicts}')
            self.stdout.write(f'  сохранено основных WhatsApp: {enrichment.saved_primary}')
            self.stdout.write(f'  создано candidates: {enrichment.candidates_created}')
            self.stdout.write(f'  обновлено candidates: {enrichment.candidates_updated}')
            self.stdout.write(f'  без контакта: {enrichment.no_contact}')
            self.stdout.write(f'  ошибок: {enrichment.errors}')

        if enrichment.lead_reports:
            self.stdout.write('ЛИДЫ:')
            for report in enrichment.lead_reports:
                phones_label = ', '.join(report.phones) if report.phones else '(нет)'
                confidence_label = report.confidence or '(нет)'
                self.stdout.write(
                    f"  @{report.username} | {phones_label} | {confidence_label} | "
                    f"{report.source_url or '(нет URL)'} | {report.action} | {report.reason}",
                )

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: база данных не изменялась.'))
        else:
            self.stdout.write(self.style.SUCCESS('Pipeline завершён.'))
