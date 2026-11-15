import importlib.util
import sys
import tempfile
import types
import unittest
from decimal import Decimal
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
    class Meta: app_label = "nightly_d_test"


class Offer(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    brand = models.CharField(max_length=100)
    article = models.CharField(max_length=120)
    cost = models.DecimalField(max_digits=14, decimal_places=2)
    stock = models.CharField(max_length=20)
    characteristics = models.JSONField(default=dict)
    kaspi_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    photo_url = models.URLField(blank=True)
    class Meta: app_label = "nightly_d_test"


class Product(models.Model):
    article = models.CharField(max_length=120)
    status = models.CharField(max_length=20, default="active")
    main_image = models.CharField(max_length=255, blank=True)
    class Meta: app_label = "nightly_d_test"


class RetailListing(models.Model):
    offer = models.OneToOneField(Offer, on_delete=models.CASCADE)
    product = models.OneToOneField(Product, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)
    class Meta: app_label = "nightly_d_test"


class VehicleFitment(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE)
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=150)
    configurations = models.JSONField(default=list)
    source_url = models.URLField(max_length=2048)
    verified = models.BooleanField(default=False)
    class Meta: app_label = "nightly_d_test"


class SupplierPhotoTask(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE)
    source_url = models.URLField(max_length=2048, blank=True)
    status = models.CharField(max_length=40, blank=True)
    note = models.TextField(blank=True)
    evidence = models.JSONField(default=dict)
    class Meta: app_label = "nightly_d_test"


class Activity(models.Model):
    user_id = models.IntegerField(null=True)
    action = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    class Meta: app_label = "nightly_d_test"


MODELS = [Supplier, Offer, Product, RetailListing, VehicleFitment, SupplierPhotoTask, Activity]
with connection.schema_editor() as editor:
    for model in MODELS: editor.create_model(model)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "reviewed_nightly_20261114_d", ROOT / "catalog" / "reviewed_nightly_20261114_d.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ROW = module.reviewed_rows()[0]


class Tests(unittest.TestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            for model in reversed(MODELS): cursor.execute("DELETE FROM " + model._meta.db_table)
        Supplier.objects.create(pk=5, name="ZakazAuto — Костанай")
        self.apps = types.SimpleNamespace(
            get_model=lambda app, name: next(model for model in MODELS if model.__name__ == name)
        )
        self.schema = types.SimpleNamespace(connection=connection)

    def add_row(self):
        offer = Offer.objects.create(
            pk=ROW["offer_id"], supplier_id=5, brand=ROW["brand"], article=ROW["article"],
            cost=ROW["cost"], stock=ROW["stock"], characteristics={"keep": True},
            kaspi_price="30120.00", photo_url="",
        )
        product = Product.objects.create(pk=ROW["product_id"], article=ROW["article"])
        RetailListing.objects.create(offer=offer, product=product)
        return offer

    def test_exact_source_photo_and_discrete_years(self):
        self.assertEqual((ROW["offer_id"], ROW["product_id"], ROW["task_id"]), (25215, 6809, 4782))
        self.assertEqual(ROW["brand"], "Parts-Mall")
        self.assertEqual({fit["model"] for fit in ROW["fitments"]}, {"Carnival I", "Sedona I"})
        self.assertTrue(all(fit["years"] == [1999, 2000, 2001] for fit in ROW["fitments"]))
        self.assertEqual(module.photo_bytes(ROW, "main")[:4], b"RIFF")
        self.assertEqual(module.photo_bytes(ROW, "thumbnail")[:4], b"RIFF")

    def test_stage_is_idempotent_and_preserves_trade_fields(self):
        offer = self.add_row()
        before = (Decimal(ROW["cost"]), offer.stock, Decimal("30120.00"), offer.photo_url)
        module.stage_review(self.apps, self.schema)
        self.assertEqual(VehicleFitment.objects.count(), 2)
        self.assertEqual(SupplierPhotoTask.objects.count(), 1)
        task = SupplierPhotoTask.objects.get()
        self.assertEqual((task.pk, task.status), (4782, module.TASK_STATUS))
        module.stage_review(self.apps, self.schema)
        self.assertEqual(VehicleFitment.objects.count(), 2)
        self.assertEqual(SupplierPhotoTask.objects.count(), 1)
        offer.refresh_from_db()
        self.assertEqual((offer.cost, offer.stock, offer.kaspi_price, offer.photo_url), before)
        self.assertTrue(offer.characteristics["keep"])
        self.assertEqual(offer.characteristics["reviewed_applicability"]["status"], "sourced")

    def test_existing_data_and_changed_offer_are_preserved(self):
        offer = self.add_row()
        VehicleFitment.objects.create(
            offer=offer, make="MANAGER", model="ENTRY", configurations=[{"note": "keep"}],
            source_url="https://example.com/manager", verified=True,
        )
        Product.objects.filter(pk=ROW["product_id"]).update(main_image="existing.webp")
        module.stage_review(self.apps, self.schema)
        self.assertEqual(list(VehicleFitment.objects.values_list("make", "model")), [("MANAGER", "ENTRY")])
        self.assertEqual(SupplierPhotoTask.objects.count(), 0)
        offer.stock = "0"
        offer.save(update_fields=["stock"])
        module.stage_review(self.apps, self.schema)
        self.assertEqual(SupplierPhotoTask.objects.count(), 0)


if __name__ == "__main__": unittest.main()
