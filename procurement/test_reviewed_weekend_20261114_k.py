import json
from pathlib import Path
from django.test import SimpleTestCase
from catalog import manufacturer_photos
from catalog import reviewed_weekend_20261114_k as reviewed

class ReviewedWeekend20261114KTests(SimpleTestCase):
    def test_exact_bounded_parts_mall_payload(self):
        row=reviewed.reviewed_rows()[0]
        self.assertEqual((row["supplier_id"],row["offer_id"],row["product_id"],row["task_id"]),(5,25251,7012,4985))
        self.assertEqual((row["brand"],row["article"],row["manufacturer_article"]),("Parts-Mall","PJC129FR","PJC-129FR"))
        configs=row["fitments"][0]["configurations"];self.assertEqual(len(configs),11);self.assertEqual(configs[1]["years"],"2011–2013");self.assertIsNone(configs[0]["date_to"]);self.assertTrue(all(x["side"]=="right" and x["drive"]=="RHD" for x in configs))
    def test_exact_official_image_parser(self):
        url="https://catalog.parts-mall.com/shop-detail?factory_no=PJC-129FR";html='<h1>PMC PJC-129FR SHOCK ABSORBER &amp; DAMPER</h1><img src="/images/PJC-129FR_0300-1F.jpg">'
        self.assertTrue(manufacturer_photos.valid_page(url,"Parts-Mall"));self.assertEqual(manufacturer_photos.matching_manufacturer_image(html,url,"Parts-Mall","PJC129FR"),"https://catalog.parts-mall.com/images/PJC-129FR_0300-1F.jpg");self.assertEqual(manufacturer_photos.matching_manufacturer_image(html,url,"Parts-Mall","PJC129FL"),"")
    def test_manifest_has_no_embedded_photo(self):
        path=Path(reviewed.__file__).parent/"data"/"reviewed_weekend_20261114_k.json";raw=json.dumps(json.loads(path.read_text()));self.assertNotIn("main_b64",raw);self.assertNotIn("thumbnail_b64",raw)
