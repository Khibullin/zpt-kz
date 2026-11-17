"""Saved competitor prices are visible as dated evidence, never buyable quotes."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from procurement.models import Membership, Offer, Preferences, Supplier


class SavedKaspiPriceListTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("saved-price-viewer")
        Membership.objects.create(user=self.user, role="admin")
        Preferences.objects.create(user=self.user, commission=Decimal("12.5"), expenses=Decimal("250"))
        self.supplier = Supplier.objects.create(name="Omega")
        self.today = timezone.localdate()
        self.verified = Offer.objects.create(
            supplier=self.supplier, brand="TEST", article="DATED-1",
            name="Рычаг с ранее найденной ценой", category="Подвеска",
            cost=Decimal("1200"), stock="5", kaspi_price=Decimal("5500"),
            match_verified=True, kaspi_url="https://kaspi.kz/shop/p/test-111111/",
            kaspi_price_checked_on=self.today - timedelta(days=10),
            supplier_checked_on=self.today,
        )
        self.unverified = Offer.objects.create(
            supplier=self.supplier, brand="TEST", article="DATED-2",
            name="Фильтр с неподтверждённой ценой", category="Фильтры",
            cost=Decimal("800"), stock="3", kaspi_price=Decimal("2300"),
            match_verified=False, kaspi_price_checked_on=None,
            supplier_checked_on=self.today,
        )
        Offer.objects.create(supplier=self.supplier, brand="TEST", article="NO-PRICE",
                             name="Без цены Kaspi", cost=1000, stock="2")
        self.client.force_login(self.user)
        self.url = reverse("procurement:dashboard") + "?mode=kaspi_prices&supplier=" + str(self.supplier.pk)

    def test_shows_saved_prices_and_dates_without_buy_action(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 2)
        self.assertContains(response, "Цены Kaspi · 2")
        self.assertContains(response, "Рычаг с ранее найденной ценой")
        self.assertContains(response, "Фильтр с неподтверждённой ценой")
        self.assertContains(response, self.verified.kaspi_price_checked_on.strftime("%d.%m.%Y"))
        self.assertContains(response, "Совпадение подтверждено")
        self.assertContains(response, "Не подтверждено")
        self.assertNotContains(response, "Без цены Kaspi")
        self.assertNotContains(response, "+ В закупку")
        self.assertEqual(Offer.objects.get(pk=self.verified.pk).kaspi_price_checked_on,
                         self.today - timedelta(days=10))

    def test_csv_keeps_original_date_and_saved_price(self):
        response = self.client.get(self.url + "&download=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("DATED-1", response.content.decode("utf-8"))
        self.assertIn("5500", response.content.decode("utf-8"))
        self.assertIn(self.verified.kaspi_price_checked_on.isoformat(), response.content.decode("utf-8"))
        self.assertIn("Сохранённая цена Kaspi", response.content.decode("utf-8"))

    def test_omega_rules_button_is_only_on_best_buy_tab(self):
        best_url = reverse("procurement:dashboard") + "?mode=best&supplier=" + str(self.supplier.pk)
        response = self.client.get(best_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="best-selection-rules"')
        self.assertContains(response, "Правила отбора")
        self.assertContains(response, "2 000 ₸ за единицу")
        self.assertContains(response, "50%")
        self.assertContains(response, "всей Республике Казахстан")
        self.assertContains(response, "Финансовый фильтр Омеги применён")
        ordinary = self.client.get(self.url)
        self.assertNotContains(ordinary, 'id="best-selection-rules"')
