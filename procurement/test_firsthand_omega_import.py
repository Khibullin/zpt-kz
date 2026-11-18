"""The Nov 17 Firsthand batch must import actual seller facts without buy approval."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .firsthand_omega_import import import_omega_firsthand, DATA_FILE
from .models import KaspiSellerSnapshot, Membership, Offer, Preferences, Supplier


class FirsthandOmegaImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "firsthand-import-admin", "firsthand@example.test", "password"
        )
        Membership.objects.create(user=self.user, role="admin")
        Preferences.objects.create(user=self.user, commission=Decimal("12.5"),
                                   expenses=Decimal("250"))
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name="Omega")
        self.rameder = self.create(
            "RAMEDER", "60E210002", "Подшипник задней ступицы",
            "https://kaspi.kz/shop/p/rameder-podshipnik-stupitsy-60e210002-115697640/",
            2266, "6", verified=True,
        )
        self.discs = self.create(
            "WXQP", "42928", "Диск тормозной задний",
            "https://kaspi.kz/shop/p/diski-wxqp-zadnie-42928-106955483/?c=750000000",
            6839, "20>",
        )
        self.conflict = self.create(
            "WXQP", "40153", "Колодки ручника",
            "https://kaspi.kz/shop/p/kolodki-wxqp-zadnie-40153-106462192/?c=750000000",
            4480, "10",
        )

    def create(self, brand, article, name, kaspi_url, cost, stock, verified=False):
        return Offer.objects.create(
            supplier=self.supplier, brand=brand, article=article, name=name,
            category="Автозапчасти", cost=cost, stock=stock,
            kaspi_url=kaspi_url, match_verified=verified,
            supplier_checked_on=date(2026, 11, 17),
            applicability="ТОЧНАЯ ПРИМЕНИМОСТЬ ОМЕГИ",
        )

    def test_import_restores_3_cities_sellers_images_and_preserves_fitment(self):
        report = import_omega_firsthand(Offer, KaspiSellerSnapshot)
        self.assertEqual(report["found"], 3)
        self.assertEqual(report["updated"], 3)
        self.assertEqual(report["identity_conflicts"], 0)
        self.assertEqual(report["city_results"], 9)
        self.assertGreater(report["seller_offers"], 10)
        self.discs.refresh_from_db()
        evidence = self.discs.characteristics["firsthand_omega_20261117"]
        self.assertEqual(len(evidence["cities"]), 3)
        self.assertEqual({c["city"] for c in evidence["cities"]},
                         {"Almaty", "Astana", "Shymkent"})
        self.assertEqual(self.discs.kaspi_price, Decimal("27999"))
        self.assertEqual(self.discs.kaspi_price_checked_on, date(2026,11,17))
        self.assertFalse(self.discs.match_verified)
        self.assertEqual(self.discs.applicability, "ТОЧНАЯ ПРИМЕНИМОСТЬ ОМЕГИ")
        self.assertFalse(self.discs.photo_url)
        self.assertTrue(evidence["images"])
        self.assertEqual(KaspiSellerSnapshot.objects.get(offer=self.discs).checked_on,
                         date(2026, 11, 17))
        self.assertEqual(len(KaspiSellerSnapshot.objects.get(offer=self.discs).sellers), 3)
        self.rameder.refresh_from_db()
        self.assertTrue(self.rameder.photo_url.startswith("https://resources.cdn-kaspi.kz/"))
        self.assertTrue(self.rameder.match_verified)

    def test_conflict_is_not_auto_verified_and_best_has_no_buy_action(self):
        import_omega_firsthand(Offer, KaspiSellerSnapshot)
        response = self.client.get(reverse("procurement:dashboard"), {
            "supplier": self.supplier.pk, "mode": "best", "best_scope": "all",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "42928")
        self.assertContains(response, "Предварительно выгодный — Firsthand")
        self.assertContains(response, "Спорное соответствие Firsthand")
        self.assertContains(response, "колодки ручника")
        self.assertContains(response, "Фото Firsthand")
        self.assertContains(response, "Продавцы и цены по сохранённым городам")
        self.assertNotContains(response, "+ В закупку")
        self.assertFalse(Offer.objects.get(pk=self.conflict.pk).match_verified)

    def test_firsthand_appears_in_new_and_latest_without_purchase_permission(self):
        from unittest.mock import patch
        import_omega_firsthand(Offer, KaspiSellerSnapshot)
        for scope in ("all", "new", "current"):
            with patch("procurement.views.timezone.localdate", return_value=date(2026, 11, 17)):
                response = self.client.get(reverse("procurement:dashboard"), {
                    "supplier": self.supplier.pk, "mode": "best", "best_scope": scope,
                })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["total"], 3, scope)
            self.assertContains(response, "42928")
            self.assertContains(response, "Спорное соответствие Firsthand")
            self.assertContains(response, "Фото Firsthand")
            self.assertNotContains(response, "+ В закупку")

    def test_import_is_idempotent(self):
        import_omega_firsthand(Offer, KaspiSellerSnapshot)
        again = import_omega_firsthand(Offer, KaspiSellerSnapshot)
        self.assertEqual(again["updated"], 0)
        self.assertEqual(KaspiSellerSnapshot.objects.count(), 3)

    def test_single_city_dataset_does_not_claim_other_cities(self):
        import_omega_firsthand(Offer, KaspiSellerSnapshot)
        self.discs.refresh_from_db()
        evidence = self.discs.characteristics["firsthand_omega_20261117"]
        evidence["cities"] = [city for city in evidence["cities"] if city["city"] == "Almaty"]
        evidence["region_scope"] = "Almaty; НЕ весь Казахстан"
        self.discs.save(update_fields=["characteristics"])
        responses = [
            self.client.get(reverse("procurement:dashboard"), {
                "supplier": self.supplier.pk, "mode": "best", "q": self.discs.article,
            }),
            self.client.get(reverse("procurement:detail", args=[self.discs.pk])),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Almaty")
            self.assertNotContains(response, "Astana")
            self.assertNotContains(response, "Shymkent")
            self.assertNotContains(response, "3 города")
            self.assertNotContains(response, "по трём городам")
