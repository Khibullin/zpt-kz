import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import manufacturer_photos
from catalog import reviewed_weekend_20261114_h as reviewed


class ReviewedWeekend20261114HTests(SimpleTestCase):
    def test_exact_bounded_photo_task(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"], row["task_id"]), (5, 25768, 7249, 5222))
        self.assertEqual((row["brand"], row["article"]), ("HYUNDAI-KIA", "495602S250"))
        self.assertEqual(row["source_url"], "https://tachka.ru/hyundai-kia/495602S250")

    def test_manifest_has_no_embedded_photo(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_h.json"
        payload = json.loads(path.read_text())
        serialized = json.dumps(payload)
        self.assertNotIn("main_b64", serialized)
        self.assertNotIn("thumbnail_b64", serialized)

    def test_tachka_exact_page_image_is_accepted(self):
        url = "https://tachka.ru/hyundai-kia/495602S250"
        image = "https://img.tachka.ru/fit-in/2000x0/brand/hyundai-kia/exact.jpeg"
        html = f'''<link rel="canonical" href="{url}">
        <script type="application/ld+json">{{"@type":"Product","brand":{{"name":"Hyundai / Kia"}},"image":"{image}","mpn":"495602S250","sku":"495602S250","name":"Полуось Hyundai / KIA. Артикул 495602S250"}}</script>'''
        self.assertTrue(manufacturer_photos.valid_page(url, "HYUNDAI-KIA"))
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "HYUNDAI-KIA", "495602S250"), image)
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "HYUNDAI-KIA", "495602S350"), "")
        self.assertFalse(manufacturer_photos.valid_page(url, "HYUNDAI / KIA"))

    def test_tachka_placeholder_is_rejected(self):
        url = "https://tachka.ru/hyundai-kia/495602S250"
        html = f'''<link rel="canonical" href="{url}">
        <script type="application/ld+json">{{"@type":"Product","brand":{{"name":"Hyundai / Kia"}},"image":"https://img.tachka.ru/other/placeholder.png","mpn":"495602S250","sku":"495602S250","name":"Полуось Hyundai / KIA. Артикул 495602S250"}}</script>'''
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "HYUNDAI-KIA", "495602S250"), "")
