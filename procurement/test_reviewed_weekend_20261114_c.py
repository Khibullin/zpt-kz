import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import reviewed_weekend_20261114_c as reviewed


class ReviewedWeekend20261114CTests(SimpleTestCase):
    def test_exact_bounded_ssangyong_payload(self):
        rows = reviewed.reviewed_rows()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"]), (5, 25114, 6829))
        self.assertEqual((row["brand"], row["article"]), ("SsangYong", "4530132100"))
        self.assertEqual(len(row["fitments"]), 1)
        fitment = row["fitments"][0]
        self.assertEqual((fitment["make"], fitment["model"]), ("SSANGYONG", "Korando Sports (Q150)"))
        self.assertEqual(fitment["years"], "2012–2017")
        self.assertNotIn("engine", fitment)
        self.assertNotIn("side", fitment)
        self.assertEqual((fitment["placement"], fitment["shock_type"]), ("rear", "gas"))
        self.assertEqual((fitment["oem_article"], fitment["mando_article"]), ("4530132100", "EX4530132100"))

    def test_manifest_is_plain_json_without_photo_payload(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_c.json"
        payload = json.loads(path.read_text())

        self.assertNotIn("main_b64", json.dumps(payload))
        self.assertNotIn("thumbnail_b64", json.dumps(payload))
