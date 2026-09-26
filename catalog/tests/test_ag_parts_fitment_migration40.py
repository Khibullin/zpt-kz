from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.test import TestCase

from catalog.models import Product


migration = import_module("catalog.migrations.0040_ag_parts_fitment_batch18")


class AgPartsBatch18MigrationTests(TestCase):
    def _create_six(self):
        for article, values in migration.BEFORE.items():
            Product.objects.create(
                article=article,
                seller_name=values["seller_name"],
                status=values["status"],
                slug=values["slug"],
                title=values["title"],
                compatibility=values["compatibility"],
                engine_compatibility=values.get("engine_compatibility", ""),
                oem_cross_references=values.get("oem_cross_references", ""),
                description=values["description"],
                pk=values["id"],
                whatsapp_number="+77713607040",
                city="Алматы",
                price=4321,
                cost_price=1234,
                stock_qty=7,
            )

    def _editor(self):
        return SimpleNamespace(connection=SimpleNamespace(alias="default"))

    def test_exact_six_update_only_text_and_can_reverse(self):
        self._create_six()
        migration.forwards(apps, self._editor())
        self.assertEqual(Product.objects.count(), 6)
        for article, fields in migration.AFTER.items():
            product = Product.objects.get(article=article)
            for field, value in fields.items():
                self.assertEqual(getattr(product, field), value)
            self.assertEqual((product.price, product.cost_price, product.stock_qty), (4321, 1234, 7))
            self.assertEqual(product.slug, migration.BEFORE[article]["slug"])
        migration.backwards(apps, self._editor())
        for article, fields in migration.BEFORE.items():
            product = Product.objects.get(article=article)
            for field in migration.AFTER[article]:
                self.assertEqual(getattr(product, field), fields[field])

    def test_drift_aborts_before_any_write(self):
        self._create_six()
        Product.objects.filter(article="CD569F2801032700").update(description="changed outside audit")
        with self.assertRaisesRegex(RuntimeError, "field description changed"):
            migration.forwards(apps, self._editor())
        self.assertEqual(Product.objects.get(article="1064000180").title, migration.BEFORE["1064000180"]["title"])

    def test_no_imported_catalogue_is_noop(self):
        migration.forwards(apps, self._editor())
        self.assertEqual(Product.objects.count(), 0)
