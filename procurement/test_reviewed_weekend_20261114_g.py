import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import reviewed_weekend_20261114_g as reviewed


class ReviewedWeekend20261114GTests(SimpleTestCase):
    def test_exact_bounded_553102p500_payload(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"]), (5, 25129, 6713))
        self.assertEqual((row["brand"], row["article"]), ("HYUNDAI-KIA", "553102P500"))
        fitment = row["fitments"][0]
        self.assertEqual((fitment["make"], fitment["model"]), ("KIA", "New Sorento R (XM)"))
        self.assertEqual(fitment["years"], "2012-07–2014-08")
        self.assertEqual((fitment["placement"], fitment["type"], fitment["restriction"]), ("rear", "gas", "4WD"))
        self.assertNotIn("engine", fitment)
        self.assertNotIn("side", fitment)

    def test_manifest_is_plain_json_without_photo_payload(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_g.json"
        payload = json.loads(path.read_text())
        self.assertNotIn("main_b64", json.dumps(payload))
        self.assertNotIn("thumbnail_b64", json.dumps(payload))
