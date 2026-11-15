import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import manufacturer_photos
from catalog import reviewed_weekend_20261114_j as reviewed


class ReviewedWeekend20261114JTests(SimpleTestCase):
    def test_exact_bounded_photo_task(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"], row["task_id"]), (5, 25765, 7250, 5223))
        self.assertEqual((row["brand"], row["article"]), ("HYUNDAI-KIA", "495602S350"))
        self.assertEqual(reviewed.TASK_STATUS, "weekend14j_ready")

    def test_manifest_has_no_embedded_photo(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_j.json"
        serialized = json.dumps(json.loads(path.read_text()))
        self.assertNotIn("main_b64", serialized)
        self.assertNotIn("thumbnail_b64", serialized)

    def test_tachka_exact_page_image_is_accepted(self):
        row = reviewed.reviewed_rows()[0]
        html = f'''<link rel="canonical" href="{row["source_url"]}">
        <script type="application/ld+json">{{"@type":"Product","brand":{{"name":"Hyundai / Kia"}},"image":"{row["verified_image_url"]}","mpn":"495602S350","sku":"495602S350","name":"Полуось Hyundai / KIA. Артикул 495602S350"}}</script>'''
        self.assertTrue(manufacturer_photos.valid_page(row["source_url"], row["brand"]))
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, row["source_url"], row["brand"], row["article"]), row["verified_image_url"])
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, row["source_url"], row["brand"], "495602S250"), "")
