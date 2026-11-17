"""Omega-specific purchase-screen rules must not change other suppliers."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from procurement.analysis import stock_status
from procurement.catalog_paging import cumulative_profitable_query
from procurement.models import Membership, Offer, Preferences, Supplier
from procurement.omega_policy import eligible_scheduled_kaspi_tasks
from procurement.models import KaspiReviewTask


class OmegaBestPolicyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="omega-policy-owner", email="owner@example.test", password="test"
        )
        Membership.objects.create(user=self.user, role="admin")
        self.preferences = Preferences.objects.create(
            user=self.user, commission=Decimal("12.5"), expenses=Decimal("250"),
        )
        self.today = timezone.localdate()
        self.omega = Supplier.objects.create(name="Omega")
        self.phaeton = Supplier.objects.create(name="Фаетон")
        self.client.force_login(self.user)

    def offer(self, article, supplier=None, **overrides):
        data = dict(
            supplier=supplier or self.omega, brand="TEST", article=article,
            name="Рычаг " + article, category="Подвеска", cost=Decimal("2000"),
            stock="20>", kaspi_price=Decimal("5000"),
            kaspi_url="https://kaspi.kz/shop/p/rycag-" + article.lower() + "-1234567/",
            match_verified=True, supplier_checked_on=self.today,
            kaspi_price_checked_on=self.today - timedelta(days=10),
        )
        data.update(overrides)
        return Offer.objects.create(**data)

    def selected(self):
        return list(cumulative_profitable_query(
            Offer.objects.all(), self.preferences, self.today,
        ).values_list("article", flat=True))

    def test_omega_2000_and_50_percent_net_roi_exact_bound_match(self):
        self.offer("GOOD")
        self.offer("TOO-CHEAP", cost=Decimal("1999"))
        self.offer("LOW-MARGIN", kaspi_price=Decimal("3500"))
        self.offer("NO-STOCK", stock="0")
        self.offer("NOT-VERIFIED", match_verified=False)
        self.offer("NO-URL", kaspi_url="")
        self.assertEqual(self.selected(), ["GOOD"])
        self.assertEqual(stock_status("20>"), "in_stock")
        self.assertEqual(stock_status("0"), "unavailable")

    def test_omega_old_price_stays_a_review_lead_not_buyable(self):
        self.offer("OLD-QUOTE")
        url = reverse("procurement:dashboard")
        response = self.client.get(url, {"mode": "best", "supplier": self.omega.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OLD-QUOTE")
        self.assertContains(response, "Предварительно выгодный")
        self.assertContains(response, "Требуется повторная проверка")
        self.assertContains(response, "по Казахстану")
        self.assertNotContains(response, "+ В закупку")
        self.assertEqual(response.context["best_scope"], "all")
        self.assertEqual(response.context["total"], 1)

    def test_fresh_almaty_quote_is_not_mislabelled_as_all_kazakhstan(self):
        self.offer("CITY-ONLY", kaspi_price_checked_on=self.today)
        response = self.client.get(reverse("procurement:dashboard"), {
            "mode": "best", "supplier": self.omega.pk,
        })
        self.assertContains(response, "CITY-ONLY")
        self.assertContains(response, "Требуется подтвердить цены и доступность по городам Казахстана")
        self.assertNotContains(response, "+ В закупку")

    def test_phaeton_retains_old_threshold(self):
        self.offer("PHAETON", supplier=self.phaeton, cost=Decimal("1000"),
                   kaspi_price=Decimal("2500"), kaspi_price_checked_on=self.today)
        self.assertIn("PHAETON", self.selected())

    def test_automatic_omega_check_skips_low_cost_and_unavailable_stock(self):
        included = self.offer("INCLUDED", match_verified=False)
        cheap = self.offer("CHEAP", cost=Decimal("1999"), match_verified=False)
        no_stock = self.offer("SOLDOUT", stock="0", match_verified=False)
        other = self.offer("OTHER", supplier=self.phaeton, cost=Decimal("1000"), match_verified=False)
        for offer in (included, cheap, no_stock, other):
            KaspiReviewTask.objects.create(offer=offer, status="pending", reason="Check")
        ids = set(eligible_scheduled_kaspi_tasks(
            KaspiReviewTask.objects.all()
        ).values_list("offer_id", flat=True))
        self.assertEqual(ids, {included.pk, other.pk})
