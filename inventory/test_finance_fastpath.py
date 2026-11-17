"""Finance URL must remain usable without unnecessary procurement reconciliation."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class FinanceFastPathTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="finance-fast-owner", email="finance-fast@example.test",
            password="password",
        )
        self.client.force_login(self.user)

    def test_legacy_finance_and_sales_skip_purchase_order_accounting(self):
        for route in ("finance", "sales"):
            # The page must not trigger an unrelated purchase reconciliation.
            # No order_summary should be imported or called for this route.
            with patch("inventory.purchasing.order_summary",
                       side_effect=AssertionError("unneeded purchase summary")):
                response = self.client.get(reverse("inventory:" + route))
            self.assertEqual(response.status_code, 200, route)
            self.assertContains(response, "Отчёт Kaspi и сопоставление")
            self.assertContains(response, "Нет данных")

    def test_overview_still_shows_accounting_balances(self):
        response = self.client.get(reverse("inventory:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Обзор учёта")
