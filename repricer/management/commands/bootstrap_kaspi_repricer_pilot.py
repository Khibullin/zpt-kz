from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product, ProductKaspiListing


@dataclass(frozen=True)
class PilotListing:
    article: str
    master_sku: str
    price: int


PILOT_LISTINGS = (
    PilotListing("X0390000206", "115801437_271928151", 3034),
    PilotListing("8104400XKY28B", "136510902_627349511", 3410),
    PilotListing("8100422XNZ01A", "129914457_677517150", 3740),
    PilotListing("T151109111", "120214535_560663169", 1700),
    PilotListing("T218107011", "131096019_815347049", 1650),
    PilotListing("S1010140400", "136896550_140830184", 1760),
    PilotListing("1109101XGW01A", "116207063_792647100", 1150),
    PilotListing("F4J163707010", "835932711", 2450),
)


class Command(BaseCommand):
    help = (
        "Создаёт/обновляет только 8 заранее проверенных Kaspi-привязок для "
        "read-only пилота репрайсера. Без --apply работает как dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Применить изменения. Без флага только показать план.",
        )

    def _resolve_product(self, article: str) -> Product:
        products = list(
            Product.objects.filter(
                article__iexact=article,
                seller_name__iexact="AG Parts",
            ).order_by("id")[:2]
        )
        if not products:
            raise CommandError(f"AG Parts товар с артикулом {article} не найден.")
        if len(products) > 1:
            raise CommandError(
                f"Для артикула {article} найдено несколько AG Parts товаров; "
                "пилот остановлен, нужна ручная сверка."
            )
        return products[0]

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        resolved = []
        for item in PILOT_LISTINGS:
            product = self._resolve_product(item.article)
            resolved.append((item, product))
            self.stdout.write(
                f"{item.article} -> product_id={product.pk} -> "
                f"Kaspi {item.master_sku} @ {item.price} ₸"
            )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: база не изменена. Для применения добавьте --apply."
                )
            )
            return

        created = 0
        updated = 0
        for item, product in resolved:
            listing, was_created = ProductKaspiListing.objects.update_or_create(
                product=product,
                master_sku=item.master_sku,
                defaults={
                    "merchant_sku": item.article,
                    "last_known_our_price": item.price,
                    "is_active": True,
                    "publish_to_kaspi": False,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'CREATED' if was_created else 'UPDATED'} listing_id={listing.pk}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Пилотные Kaspi-привязки готовы: created={created}, updated={updated}. "
                "Публикация в Kaspi не включалась."
            )
        )
