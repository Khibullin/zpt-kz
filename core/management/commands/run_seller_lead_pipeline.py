from django.core.management.base import BaseCommand, CommandError

from core.models import SellerLeadPipelineRun
from core.services.seller_lead_pipeline import (
    DEFAULT_CATEGORY,
    DEFAULT_CITY,
    DEFAULT_LEAD_LIMIT,
    DEFAULT_MAX_QUERIES_PER_LEAD,
    DEFAULT_SEARCH_LIMIT,
    SellerLeadPipelineConfigError,
    run_seller_lead_pipeline,
)
from core.services.seller_lead_pipeline_execution import (
    execute_managed_seller_lead_pipeline,
    format_run_duration,
)
from core.services.seller_lead_pipeline_guard import (
    DEFAULT_COOLDOWN_MINUTES,
    PipelineLockBusy,
    PipelineRunLock,
    validate_cooldown_minutes,
)
from core.services.seller_lead_search import (
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    get_seller_search_settings,
)
from core.services.seller_lead_search_rotation import (
    PipelineSearchConfigError,
    SEARCH_ROTATION_PROFILES,
    resolve_pipeline_search,
)


class Command(BaseCommand):
    help = (
        'Ограниченный pipeline: поиск новых Instagram-профилей и WhatsApp '
        'только для лидов, созданных в текущем запуске.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--city', type=str, default=DEFAULT_CITY, help='Город поиска')
        parser.add_argument('--category', type=str, default=DEFAULT_CATEGORY, help='Категория лида')
        parser.add_argument(
            '--search-term',
            type=str,
            default=None,
            help='Явный поисковый термин для Brave (без ротации)',
        )
        parser.add_argument(
            '--rotate-search-term',
            action='store_true',
            help='Выбрать search_term и category по ежедневной ротации',
        )
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
            '--cooldown-minutes',
            type=int,
            default=DEFAULT_COOLDOWN_MINUTES,
            help='Минимальный интервал между live-запусками (0 отключает)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Обойти cooldown, но не активную блокировку',
        )
        parser.add_argument(
            '--trigger',
            type=str,
            choices=[SellerLeadPipelineRun.TRIGGER_MANUAL, SellerLeadPipelineRun.TRIGGER_CRON],
            default=SellerLeadPipelineRun.TRIGGER_MANUAL,
            help='Источник запуска: manual или cron',
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
        try:
            resolved_search = resolve_pipeline_search(
                category=options['category'],
                search_term=options['search_term'],
                rotate_search_term=options['rotate_search_term'],
            )
        except PipelineSearchConfigError as exc:
            raise CommandError(str(exc)) from exc

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
            validate_cooldown_minutes(options['cooldown_minutes'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        valid_triggers = {
            SellerLeadPipelineRun.TRIGGER_MANUAL,
            SellerLeadPipelineRun.TRIGGER_CRON,
        }
        if options['trigger'] not in valid_triggers:
            raise CommandError(f"Недопустимый trigger: {options['trigger']}")

        if dry_run:
            self._write_pipeline_run_header(options, resolved_search)
            self._run_dry_pipeline(options, settings_data, resolved_search)
            return

        self._write_pipeline_run_header(options, resolved_search)
        try:
            managed = execute_managed_seller_lead_pipeline(
                city=options['city'],
                category=resolved_search.category,
                search_term=resolved_search.search_term,
                search_limit=options['search_limit'],
                lead_limit=options['lead_limit'],
                max_queries_per_lead=options['max_queries_per_lead'],
                skip_discovery=options['skip_discovery'],
                skip_enrichment=options['skip_enrichment'],
                cooldown_minutes=options['cooldown_minutes'],
                force_run=options['force'],
                trigger=options['trigger'],
                resolved_search=resolved_search,
                search_settings=settings_data,
            )
        except SellerLeadPipelineConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        if managed.lock_busy:
            self.stdout.write(
                'Pipeline уже выполняется другим процессом. Запуск пропущен.',
            )
            return

        if managed.cooldown_blocked and managed.run is not None:
            previous = managed.cooldown_check.previous_run if managed.cooldown_check else None
            self.stdout.write('STATUS: skipped')
            self.stdout.write(f'RUN UUID: {managed.run.run_uuid}')
            self.stdout.write(f'Причина: {managed.run.skip_reason}')
            if previous is not None and managed.cooldown_check is not None:
                self.stdout.write(f'Предыдущий run UUID: {previous.run_uuid}')
                self.stdout.write(f'Предыдущий запуск: {previous.started_at:%Y-%m-%d %H:%M}')
                self.stdout.write(
                    f'Осталось cooldown: ~{managed.cooldown_check.minutes_remaining} мин.',
                )
            return

        if managed.run is None or managed.stats is None:
            raise CommandError('Pipeline завершился без результата.')

        self._write_pipeline_stats(managed.stats, resolved_search.search_term)
        self._write_live_footer(managed.run)
        self.stdout.write(self.style.SUCCESS('Pipeline завершён.'))

    def _run_dry_pipeline(self, options, settings_data, resolved_search):
        try:
            with PipelineRunLock():
                stats = run_seller_lead_pipeline(
                    city=options['city'],
                    category=resolved_search.category,
                    search_term=resolved_search.search_term,
                    search_limit=options['search_limit'],
                    lead_limit=options['lead_limit'],
                    max_queries_per_lead=options['max_queries_per_lead'],
                    dry_run=True,
                    skip_discovery=options['skip_discovery'],
                    skip_enrichment=options['skip_enrichment'],
                    search_settings=settings_data,
                )
        except PipelineLockBusy:
            self.stdout.write(
                'Pipeline уже выполняется другим процессом. Запуск пропущен.',
            )
            return
        except SellerLeadPipelineConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchError as exc:
            raise CommandError(str(exc)) from exc

        self._write_pipeline_stats(stats, resolved_search.search_term)
        self.stdout.write(self.style.WARNING('Dry-run: база данных не изменялась.'))

    def _write_pipeline_run_header(self, options, resolved_search):
        self.stdout.write('PIPELINE RUN:')
        self.stdout.write(f"  trigger: {options['trigger']}")
        self.stdout.write(f"  city: {options['city']}")
        self.stdout.write(f"  search term: {resolved_search.search_term}")
        self.stdout.write(f"  stored category: {resolved_search.category}")
        if resolved_search.rotation_enabled:
            position = (resolved_search.rotation_index or 0) + 1
            total = len(SEARCH_ROTATION_PROFILES)
            self.stdout.write('  rotation: enabled')
            self.stdout.write(f"  rotation profile: {resolved_search.rotation_slug}")
            self.stdout.write(f"  rotation position: {position}/{total}")
        else:
            self.stdout.write('  rotation: disabled')
        self.stdout.write(f"  search-limit: {options['search_limit']}")
        self.stdout.write(f"  lead-limit: {options['lead_limit']}")
        self.stdout.write(f"  max-queries-per-lead: {options['max_queries_per_lead']}")
        self.stdout.write(f"  cooldown-minutes: {options['cooldown_minutes']}")
        self.stdout.write(f"  force: {options['force']}")

    def _write_live_footer(self, run: SellerLeadPipelineRun):
        self.stdout.write('RUN RESULT:')
        self.stdout.write(f'  run UUID: {run.run_uuid}')
        self.stdout.write(f'  status: {run.status}')
        self.stdout.write(f'  started_at: {run.started_at:%Y-%m-%d %H:%M:%S}')
        if run.finished_at:
            self.stdout.write(f'  finished_at: {run.finished_at:%Y-%m-%d %H:%M:%S}')
        self.stdout.write(f'  duration: {format_run_duration(run)}')

    def _write_pipeline_stats(self, stats, search_term: str):
        discovery = stats.discovery
        enrichment = stats.enrichment

        self.stdout.write('DISCOVERY:')
        self.stdout.write(f'  search term: {search_term}')
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
