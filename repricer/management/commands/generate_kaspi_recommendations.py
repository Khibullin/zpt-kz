from django.core.management.base import BaseCommand, CommandError

from repricer.models import KaspiRepricerRule
from repricer.services import RepricerConfigurationError, generate_recommendation


class Command(BaseCommand):
    help = (
        "Генерирует и сохраняет рекомендации Kaspi-репрайсера. "
        "Никаких цен в Kaspi не отправляет."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--listing-id",
            type=int,
            help="Ограничить расчёт одним Kaspi-листингом.",
        )
        parser.add_argument(
            "--max-age-minutes",
            type=int,
            default=60,
            help="Считать актуальными цены конкурентов не старше N минут (по умолчанию 60).",
        )

    def handle(self, *args, **options):
        max_age_minutes = options["max_age_minutes"]
        if max_age_minutes <= 0:
            raise CommandError("--max-age-minutes должен быть больше нуля.")

        rules = KaspiRepricerRule.objects.filter(is_enabled=True).select_related(
            "listing__product"
        )
        if options.get("listing_id"):
            rules = rules.filter(listing_id=options["listing_id"])

        rules = list(rules)
        if not rules:
            raise CommandError("Подходящих включённых правил репрайсера не найдено.")

        counters = {"LOWER": 0, "RAISE": 0, "HOLD": 0, "ERROR": 0}
        for rule in rules:
            article = rule.listing.product.article or f"product-{rule.listing.product_id}"
            try:
                result = generate_recommendation(
                    rule=rule,
                    max_age_minutes=max_age_minutes,
                )
            except RepricerConfigurationError as exc:
                counters["ERROR"] += 1
                self.stderr.write(self.style.ERROR(f"{article}: {exc}"))
                continue

            recommendation = result.recommendation
            counters[recommendation.action] += 1
            best = (
                f"{recommendation.best_competitor_price} ₸"
                if recommendation.best_competitor_price is not None
                else "нет данных"
            )
            self.stdout.write(
                f"{article} | {recommendation.current_price} → "
                f"{recommendation.recommended_price} ₸ | "
                f"best={best} | competitors={result.competitor_count} | "
                f"{recommendation.action}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Готово: "
                f"снизить={counters['LOWER']}, "
                f"повысить={counters['RAISE']}, "
                f"оставить={counters['HOLD']}, "
                f"ошибок={counters['ERROR']}."
            )
        )
        self.stdout.write(
            self.style.WARNING("Режим рекомендаций: цены в Kaspi не изменялись.")
        )
