import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import reviewed_weekend_20261114_e as reviewed


class ReviewedWeekend20261114ETests(SimpleTestCase):
    def test_exact_manufacturer_trialli_payload(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"]), (5, 25170, 6841))
        self.assertEqual((row["brand"], row["article"]), ("TRIALLI", "AG 03007"))
        self.assertEqual(len(row["fitments"]), 11)
        self.assertEqual({fit["make"] for fit in row["fitments"]}, {"UAZ"})
        self.assertEqual({fit["side"] for fit in row["fitments"]}, {"left/right"})
        self.assertEqual({fit["package_quantity"] for fit in row["fitments"]}, {1})
        self.assertEqual({fit["quantity_per_vehicle"] for fit in row["fitments"]}, {2})
        self.assertIn("2009-н.в.", {fit["years"] for fit in row["fitments"]})

    def test_manifest_is_plain_json_without_photo_payload(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_e.json"
        payload = json.loads(path.read_text())
        self.assertNotIn("main_b64", json.dumps(payload))
        self.assertNotIn("thumbnail_b64", json.dumps(payload))
