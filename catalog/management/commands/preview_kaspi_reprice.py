from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from catalog.kaspi_repricing import RepricingPolicy, recommend_kaspi_price
from catalog.models import ProductKaspiListing


class Command(BaseCommand):
    help = (
        "Показывает рекомендацию репрайсера для одной Kaspi-привязки. "
        "Команда ничего не меняет ни в БД, ни в Kaspi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--listing-id", type=int, required=True)
        parser.add_argument(
            "--competitor-price",
            action="append",
            default=[],
            help="Цена конкурента. Параметр можно повторить несколько раз.",
        )
        parser.add_argument("--min-price", required=True, help="Минимально допустимая цена.")
        parser.add_argument("--step", default="1", help="Шаг относительно лучшей цены, по умолчанию 1 ₸.")
        parser.add_argument(
            "--max-change-percent",
            default="10",
            help="Максимальное изменение за один расчёт, по умолчанию 10%%.",
        )
        parser.add_argument(
            "--no-raise",
            action="store_true",
            help="Не рекомендовать повышение цены.",
        )

    @staticmethod
    def _decimal(value: str, option_name: str) -> Decimal:
        try:
            return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise CommandError(f"Некорректное значение {option_name}: {value}") from exc

    def handle(self, *args, **options):
        try:
            listing = ProductKaspiListing.objects.select_related("product").get(
                pk=options["listing_id"]
            )
        except ProductKaspiListing.DoesNotExist as exc:
            raise CommandError("Kaspi-привязка с таким listing-id не найдена.") from exc

        current_price = listing.last_known_our_price
        if current_price is None:
            raise CommandError(
                "У Kaspi-привязки ещё нет last_known_our_price. "
                "Не подменяем цену Kaspi ценой сайта ZPT.KZ."
            )

        competitor_prices = [
            self._decimal(value, "--competitor-price")
            for value in options["competitor_price"]
        ]
        policy = RepricingPolicy(
            min_price=self._decimal(options["min_price"], "--min-price"),
            price_step=self._decimal(options["step"], "--step"),
            max_change_percent=self._decimal(
                options["max_change_percent"], "--max-change-percent"
            ),
            allow_raise=not options["no_raise"],
        )
        decision = recommend_kaspi_price(
            current_price=Decimal(current_price),
            competitor_prices=competitor_prices,
            policy=policy,
        )

        article = listing.product.article or f"product-{listing.product_id}"
        self.stdout.write(f"ZPT артикул: {article}")
        self.stdout.write(f"Kaspi master SKU: {listing.master_sku}")
        self.stdout.write(f"Kaspi merchant SKU: {listing.merchant_sku}")
        self.stdout.write(f"Текущая цена: {decision.current_price} ₸")
        self.stdout.write(
            "Лучшая цена конкурента: "
            + (
                f"{decision.best_competitor_price} ₸"
                if decision.best_competitor_price is not None
                else "нет данных"
            )
        )
        self.stdout.write(f"Наша ценовая позиция: {decision.market_position or '—'}")
        self.stdout.write(f"Действие: {decision.action}")
        self.stdout.write(f"Рекомендованная цена: {decision.recommended_price} ₸")
        self.stdout.write(f"Причина: {decision.reason}")
        self.stdout.write(self.style.WARNING("DRY RUN: цена в Kaspi не изменялась."))
