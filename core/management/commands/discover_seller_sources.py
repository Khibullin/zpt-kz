"""Manual Seller Discovery search. Not wired to cron or the Instagram pipeline.

Writes SellerLead rows only. Does not create Seller, User, SellerProfile, or
Product, does not send messages, and does not turn on receive_requests.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.seller_discovery_providers.base import (
    DiscoveryProviderConfigError,
    require_discovery_provider,
)
from core.services.seller_discovery_providers.catalog import (
    DISCOVERY_CITIES,
    DISMANTLER_DIRECTIONS,
    KZ_DISCOVERY_CITY_NAMES,
    NEW_PARTS_DIRECTIONS,
)
from core.services.seller_discovery_runner import (
    SellerDiscoveryRunError,
    run_seller_discovery,
)

PROVIDER_ALIASES = {
    'two_gis': 'two_gis',
    '2gis': 'two_gis',
    'brave': 'brave',
    'brave_search': 'brave',
}


class Command(BaseCommand):
    help = (
        'Ищет магазины автозапчастей в 2GIS и Brave и сохраняет SellerLead. '
        'Не создаёт продавцов, не отправляет сообщения и не включает приём заявок.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            action='append',
            dest='providers',
            default=[],
            help='Источник: two_gis или brave. Можно передать несколько раз.',
        )
        parser.add_argument(
            '--city',
            action='append',
            dest='cities',
            default=[],
            help='Город из реестра discovery Казахстана. Можно передать несколько раз.',
        )
        parser.add_argument(
            '--mvp-cities',
            action='store_true',
            help='Искать в Алматы, Астане и Шымкенте.',
        )
        parser.add_argument(
            '--all-kz-cities',
            action='store_true',
            help='Крупные города и областные центры. За один запуск берётся не больше --city-limit.',
        )
        parser.add_argument(
            '--city-limit',
            type=int,
            default=None,
            help='Сколько городов брать из списка. Для --all-kz-cities по умолчанию 3, максимум 5.',
        )
        parser.add_argument(
            '--city-offset',
            type=int,
            default=0,
            help='С какой позиции стабильного списка городов брать пачку. Следующий запуск: --city-offset <next_city_offset>.',
        )
        parser.add_argument(
            '--direction-group',
            choices=('new_parts', 'dismantlers'),
            default='',
            help='Набор поисковых фраз. Тип бизнеса по этому флагу не назначается.',
        )
        parser.add_argument(
            '--direction',
            action='append',
            dest='directions',
            default=[],
            help='Поисковое направление. Без флага берутся первые направления каталога.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Результатов на страницу или на запрос Brave. Для 2GIS по умолчанию берётся SELLER_DISCOVERY_2GIS_PAGE_SIZE.',
        )
        parser.add_argument('--max-hits', type=int, default=20, help='Общий предел наблюдений, максимум 50.')
        parser.add_argument(
            '--max-queries',
            type=int,
            default=None,
            help=(
                'Предел запросов к API, максимум 20. Без флага бюджет равен '
                'городам × направлениям × провайдерам. Явное меньшее значение '
                'обрабатывает пачку частично.'
            ),
        )
        parser.add_argument(
            '--max-pages',
            type=int,
            default=None,
            help='Сколько страниц 2GIS читать. По умолчанию SELLER_DISCOVERY_2GIS_MAX_PAGES, жёсткий потолок 5.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать, что было бы сохранено, без записи в базу.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать SellerLead. Не отправляет приглашения и заявки.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        apply = bool(options['apply'])
        if dry_run and apply:
            raise CommandError('Укажите только один режим: --dry-run или --apply.')
        if not dry_run and not apply:
            raise CommandError('Укажите --dry-run или --apply. Без режима запись и запросы не выполняются.')

        providers = _canonical_providers(options['providers'])
        try:
            for provider_name in providers:
                require_discovery_provider(provider_name)
        except DiscoveryProviderConfigError as exc:
            raise CommandError(str(exc)) from exc

        city_modes = (
            bool(options['cities']),
            bool(options['mvp_cities']),
            bool(options['all_kz_cities']),
        )
        if sum(city_modes) != 1:
            raise CommandError('Укажите ровно один режим города: --city, --mvp-cities или --all-kz-cities.')
        if options['all_kz_cities']:
            cities = list(KZ_DISCOVERY_CITY_NAMES)
            city_limit = 3 if options['city_limit'] is None else options['city_limit']
        elif options['mvp_cities']:
            cities = list(DISCOVERY_CITIES)
            city_limit = options['city_limit']
        else:
            cities = list(options['cities'])
            city_limit = options['city_limit']
        if options['directions'] and options['direction_group']:
            raise CommandError('Укажите --direction или --direction-group, не оба.')
        if options['direction_group'] == 'new_parts':
            directions = list(NEW_PARTS_DIRECTIONS)
        elif options['direction_group'] == 'dismantlers':
            directions = list(DISMANTLER_DIRECTIONS)
        else:
            directions = options['directions'] or None
        self._require_keys(providers)

        try:
            stats = run_seller_discovery(
                provider_names=providers,
                cities=cities,
                directions=directions,
                limit=options['limit'],
                max_hits=options['max_hits'],
                max_queries=options['max_queries'],
                max_pages=options['max_pages'],
                dry_run=dry_run,
                city_limit=city_limit,
                city_offset=options['city_offset'],
            )
        except (SellerDiscoveryRunError, DiscoveryProviderConfigError) as exc:
            raise CommandError(str(exc)) from exc

        mode = 'dry-run' if dry_run else 'apply'
        self.stdout.write(f'Режим: {mode}')
        limit_label = 'all' if stats.city_limit is None else str(stats.city_limit)
        self.stdout.write(f'city_offset={stats.city_offset}')
        self.stdout.write(f'city_limit={limit_label}')
        self.stdout.write(f'cities_selected={len(stats.cities_selected)}')
        self.stdout.write(f"selected cities: {', '.join(stats.cities_selected)}")
        self.stdout.write(f'cities_completed={len(stats.cities_completed)}')
        self.stdout.write(f"completed cities: {', '.join(stats.cities_completed)}")
        self.stdout.write(f'next_city_offset={stats.next_city_offset}')
        self.stdout.write(f'Следующий запуск: --city-offset {stats.next_city_offset}')
        self.stdout.write(f"Источники: {', '.join(stats.providers)}")
        self.stdout.write(f"Направления: {', '.join(stats.directions)}")
        self.stdout.write(f'Запросов: {stats.queries_executed}')
        self.stdout.write(f'Наблюдений: {stats.hits_received}')
        if dry_run:
            self.stdout.write(f'Будет создано SellerLead: {stats.created}')
            self.stdout.write(f'Будет обновлено SellerLead: {stats.updated}')
        else:
            self.stdout.write(f'Создано SellerLead: {stats.created}')
            self.stdout.write(f'Обновлено SellerLead: {stats.updated}')
        self.stdout.write(f'Возможных дублей отмечено: {stats.possible_duplicates}')
        self.stdout.write(
            f'Типы: new_parts={stats.new_parts} dismantlers={stats.dismantlers} '
            f'mixed={stats.mixed} unknown={stats.unknown}',
        )
        self.stdout.write(f'Пропущено: {stats.skipped}')
        self.stdout.write(f'Ошибок: {stats.errors}')
        for message in stats.error_messages[:5]:
            self.stdout.write(self.style.WARNING(message))
        for outcome in stats.outcomes[:20]:
            lead_label = f'#{outcome.seller_lead_id}' if outcome.seller_lead_id else 'новый'
            self.stdout.write(
                f'  [{outcome.action}] {outcome.name} | {outcome.city} | '
                f'{outcome.provider} | {lead_label} | {outcome.match_reason}',
            )
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Discovery завершён. Продавцы, пользователи и товары не создавались. '
                'Сообщения не отправлялись.',
            ))

    def _require_keys(self, providers: list[str]) -> None:
        if 'brave' in providers and not (getattr(settings, 'BRAVE_SEARCH_API_KEY', '') or '').strip():
            raise CommandError(
                'BRAVE_SEARCH_API_KEY не задан. Укажите ключ в переменных окружения.',
            )
        if 'two_gis' in providers and not (getattr(settings, 'TWO_GIS_API_KEY', '') or '').strip():
            raise CommandError(
                'TWO_GIS_API_KEY не задан. Укажите ключ в переменных окружения.',
            )


def _canonical_providers(names: list[str]) -> list[str]:
    if not names:
        raise CommandError('Укажите --provider two_gis или --provider brave.')
    canonical: list[str] = []
    for name in names:
        key = str(name or '').strip().lower().replace('-', '_')
        resolved = PROVIDER_ALIASES.get(key)
        if resolved is None:
            raise CommandError(f'Неизвестный источник discovery: {name}')
        if resolved not in canonical:
            canonical.append(resolved)
    return canonical
