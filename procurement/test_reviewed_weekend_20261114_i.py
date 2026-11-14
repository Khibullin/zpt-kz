import json
from pathlib import Path

from django.test import SimpleTestCase

from catalog import manufacturer_photos
from catalog import reviewed_weekend_20261114_i as reviewed


class ReviewedWeekend20261114ITests(SimpleTestCase):
    def test_exact_bounded_photo_task(self):
        row = reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"], row["offer_id"], row["product_id"], row["task_id"]), (5, 25784, 7360, 5333))
        self.assertEqual((row["brand"], row["article"]), ("SsangYong", "2132009052"))
        self.assertEqual(reviewed.TASK_STATUS, "weekend14i_ready")

    def test_manifest_has_no_embedded_photo(self):
        path = Path(reviewed.__file__).parent / "data" / "reviewed_weekend_20261114_i.json"
        serialized = json.dumps(json.loads(path.read_text()))
        self.assertNotIn("main_b64", serialized)
        self.assertNotIn("thumbnail_b64", serialized)

    def test_autoopt_exact_page_image_is_accepted(self):
        url = "https://www.autoopt.ru/catalog/723986-exact_ssangyong_part"
        image = "https://www.autoopt.ru/product_pictures/big/ccd/723986.jpg"
        html = f'''<link rel="canonical" href="{url}">
        <meta property="og:title" content="Вентилятор SSANGYONG OE - 2132009052">
        <meta property="og:description" content="2132009052 Вентилятор SSANGYONG в сборе">
        <meta property="og:image" content="{image}">'''
        self.assertTrue(manufacturer_photos.valid_page(url, "SsangYong"))
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "SsangYong", "2132009052"), image)
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "SsangYong", "2132009053"), "")
        self.assertFalse(manufacturer_photos.valid_page(url, "ASVA"))

    def test_autoopt_other_product_image_is_rejected(self):
        url = "https://www.autoopt.ru/catalog/723986-exact_ssangyong_part"
        html = f'''<link rel="canonical" href="{url}">
        <meta property="og:title" content="Вентилятор SSANGYONG OE - 2132009052">
        <meta property="og:description" content="2132009052 Вентилятор SSANGYONG в сборе">
        <meta property="og:image" content="https://www.autoopt.ru/product_pictures/big/ccd/999999.jpg">'''
        self.assertEqual(manufacturer_photos.matching_manufacturer_image(html, url, "SsangYong", "2132009052"), "")
