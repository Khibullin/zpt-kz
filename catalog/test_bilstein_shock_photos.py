from unittest import TestCase
from catalog.manufacturer_photos import matching_manufacturer_image, valid_page


class BilsteinShockPhotoTests(TestCase):
    def test_exact_identity_and_source_boundary(self):
        url = 'https://cart.bilsteinus.com/shock-19-135021.html'
        image = 'https://cart.bilsteinus.com/media/catalog/product/a/p/apa_19-135021_1_20261103121715.jpg'
        html = '<input id="pro_part_number" value="19-135021"><img id="pdpMainImg" src="' + image + '">'
        self.assertEqual(matching_manufacturer_image(html, url, 'BILSTEIN', '19-135021'), image)
        for bad_url, brand, article, page in [
            (url, 'BILSTEIN', '19-135021K', html),
            (url.replace('shock-', 'strut-'), 'BILSTEIN', '19-135021', html),
            (url + '?other=1', 'BILSTEIN', '19-135021', html),
            (url, 'OTHER', '19-135021', html),
            (url, 'BILSTEIN', '19-135021', html.replace('value="19-135021"', 'value="19-246390"')),
            (url, 'BILSTEIN', '19-135021', html.replace('apa_19-135021', 'apa_19-246390')),
            (url, 'BILSTEIN', '19-135021', html.replace('/media/', '/other/')),
        ]:
            with self.subTest(url=bad_url, brand=brand, article=article, page=page):
                self.assertEqual(matching_manufacturer_image(page, bad_url, brand, article), '')
        self.assertTrue(valid_page('https://cart.bilsteinus.com/strut-22-119261.html', 'BILSTEIN'))
