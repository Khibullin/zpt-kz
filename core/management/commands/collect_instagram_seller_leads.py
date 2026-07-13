from django.core.management.base import BaseCommand, CommandError

from core.services.seller_lead_search import (
    SellerLeadSearchConfigError,
    SellerLeadSearchError,
    collect_instagram_seller_leads,
    get_seller_search_settings,
)


class Command(BaseCommand):
    help = 'Ищет Instagram-профили продавцов автозапчастей через поисковый API и сохраняет SellerLead.'

    def add_arguments(self, parser):
        parser.add_argument('--city', type=str, default=None, help='Город для поиска')
        parser.add_argument('--category', type=str, default=None, help='Категория для поиска')
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Максимум результатов на один поисковый запрос',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать найденные профили без сохранения в базу',
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
            stats = collect_instagram_seller_leads(
                city=options['city'],
                category=options['category'],
                limit=max(1, options['limit']),
                dry_run=dry_run,
                search_settings=settings_data,
            )
        except SellerLeadSearchConfigError as exc:
            raise CommandError(str(exc)) from exc
        except SellerLeadSearchError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Найдено результатов: {stats.results_found}")
        self.stdout.write(f"Распознано Instagram-профилей: {stats.profiles_parsed}")
        self.stdout.write(f"Создано новых SellerLead: {stats.created}")
        self.stdout.write(f"Пропущено дублей: {stats.duplicates_skipped}")
        self.stdout.write(f"Отклонено ссылок: {stats.links_rejected}")
        self.stdout.write(f"Ошибок: {stats.errors}")

        if dry_run and stats.dry_run_profiles:
            self.stdout.write('Найденные профили (dry-run):')
            for profile in stats.dry_run_profiles:
                self.stdout.write(
                    f"  @{profile.username} | {profile.city} | {profile.category} | {profile.profile_url}",
                )

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS('Поиск Instagram-профилей завершён.'))
