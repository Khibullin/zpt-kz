import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import reviewed_weekend_20261114_d as reviewed


class ReviewedWeekend20261114DTests(SimpleTestCase):
    def test_exact_bounded_jashi_payload(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"]), (5, 25082, 6786))
        self.assertEqual((row["brand"], row["article"]), ("JASHI", "C281F260202-0200"))
        self.assertEqual([fit["years"] for fit in row["fitments"]], ["2022", "2023", "2024"])
        for fitment in row["fitments"]:
            self.assertEqual((fitment["make"], fitment["model"]), ("CHANGAN", "UNI-V"))
            self.assertEqual(fitment["engine"], "1.5L Turbo")
            self.assertEqual((fitment["placement"], fitment["side"]), ("front", "left"))
            self.assertEqual(fitment["package_quantity"], 1)
            self.assertIn("Комплектная", fitment["details"])

    def test_manifest_is_plain_json_without_photo_payload(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_d.json"
        payload = json.loads(path.read_text())
        self.assertNotIn("main_b64", json.dumps(payload))
        self.assertNotIn("thumbnail_b64", json.dumps(payload))
