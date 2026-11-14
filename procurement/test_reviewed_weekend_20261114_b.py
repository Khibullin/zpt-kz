import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import reviewed_weekend_20261114_b as reviewed


class ReviewedWeekend20261114BTests(SimpleTestCase):
    def test_exact_bounded_mando_payload(self):
        rows = reviewed.reviewed_rows()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"]), (5, 25268, 6994))
        self.assertEqual((row["brand"], row["article"]), ("Mando", "EX54661A7100"))
        self.assertEqual(len(row["fitments"]), 1)
        fitment = row["fitments"][0]
        self.assertEqual((fitment["make"], fitment["model"]), ("KIA", "K3 (YD)"))
        self.assertEqual(fitment["date_from"], "2012-10")
        self.assertIsNone(fitment["date_to"])
        self.assertNotIn("engine", fitment)
        self.assertEqual((fitment["placement"], fitment["side"], fitment["shock_type"]), ("front", "right", "gas"))

    def test_manifest_is_plain_json_without_photo_payload(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_b.json"
        payload = json.loads(path.read_text())

        self.assertNotIn("main_b64", json.dumps(payload))
        self.assertNotIn("thumbnail_b64", json.dumps(payload))
