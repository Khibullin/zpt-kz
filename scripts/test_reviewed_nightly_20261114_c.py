import importlib.util
import tempfile
import types
import unittest
from pathlib import Path

from django.conf import settings

TMP = tempfile.TemporaryDirectory()
settings.configure(
    INSTALLED_APPS=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    SECRET_KEY="test",
)
import django

django.setup()
from django.db import connection, models


class Supplier(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "nightly_test_c"


class Offer(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    brand = models.CharField(max_length=100)
    article = models.CharField(max_length=120)
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    stock = models.CharField(max_length=20)
    characteristics = models.JSONField(default=dict)
    kaspi_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    photo_url = models.URLField(blank=True)

    class Meta:
        app_label = "nightly_test_c"


class Product(models.Model):
    article = models.CharField(max_length=120)

    class Meta:
        app_label = "nightly_test_c"


class RetailListing(models.Model):
    offer = models.OneToOneField(Offer, on_delete=models.CASCADE)
    product = models.OneToOneField(Product, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = "nightly_test_c"


class VehicleFitment(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE)
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=150)
    configurations = models.JSONField(default=list)
    source_url = models.URLField(max_length=2048)
    verified = models.BooleanField(default=False)

    class Meta:
        app_label = "nightly_test_c"


class Activity(models.Model):
    user_id = models.IntegerField(null=True)
    action = models.CharField(max_length=100)
    details = models.JSONField(default=dict)

    class Meta:
        app_label = "nightly_test_c"


MODELS = [Supplier, Offer, Product, RetailListing, VehicleFitment, Activity]
with connection.schema_editor() as editor:
    for model in MODELS:
        editor.create_model(model)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reviewed_nightly_20261114_c", ROOT / "catalog" / "reviewed_nightly_20261114_c.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            for model in reversed(MODELS):
                cursor.execute("DELETE FROM " + model._meta.db_table)
        Supplier.objects.create(pk=5, name="ZakazAuto — Костанай")
        self.apps = types.SimpleNamespace(
            get_model=lambda app, name: next(model for model in MODELS if model.__name__ == name)
        )
        self.schema = types.SimpleNamespace(connection=connection)

    def add_row(self):
        row = module.reviewed_rows()[0]
        offer = Offer.objects.create(
            pk=row["offer_id"], supplier_id=5, brand=row["brand"], article=row["article"],
            cost=row["cost"], stock=row["stock"], characteristics={"keep": True},
            kaspi_price="99999.00", photo_url="",
        )
        product = Product.objects.create(pk=row["product_id"], article=row["article"])
        RetailListing.objects.create(offer=offer, product=product)
        return offer

    def test_exact_sources_and_discrete_years(self):
        row = module.reviewed_rows()[0]
        self.assertEqual(row["article"], "2905110AKZ16A")
        self.assertEqual(row["fitments"][1]["years"], [2021, 2022, 2023, 2024])
        self.assertIsNone(row["fitments"][0]["date_to"])

    def test_stage_is_idempotent_and_preserves_trade_fields(self):
        offer = self.add_row()
        offer.refresh_from_db()
        before = (offer.cost, offer.stock, offer.kaspi_price, offer.photo_url)
        module.stage_review(self.apps, self.schema)
        self.assertEqual(VehicleFitment.objects.count(), 2)
        module.stage_review(self.apps, self.schema)
        self.assertEqual(VehicleFitment.objects.count(), 2)
        offer.refresh_from_db()
        self.assertEqual((offer.cost, offer.stock, offer.kaspi_price, offer.photo_url), before)
        self.assertEqual(offer.characteristics["keep"], True)

    def test_changed_article_is_skipped(self):
        offer = self.add_row()
        offer.article = "2905110AKZ16A4"
        offer.save(update_fields=["article"])
        module.stage_review(self.apps, self.schema)
        self.assertEqual(VehicleFitment.objects.count(), 0)


if __name__ == "__main__":
    unittest.main()
