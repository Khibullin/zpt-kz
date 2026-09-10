from django.test import TestCase

from catalog.models import Brand, Country
from core.seo_middleware import REVIEWED_BRAND_LANDING_PATHS


class SeoBrandFilterRedirectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='SEO Redirect Test Country')
        cls.reviewed = {
            name: Brand.objects.create(country=country, name=name.title())
            for name in REVIEWED_BRAND_LANDING_PATHS
        }
        cls.other = Brand.objects.create(country=country, name='Toyota')

    def test_reviewed_brand_only_filters_redirect_permanently(self):
        for name, brand in self.reviewed.items():
            with self.subTest(name=name):
                response = self.client.get('/', {'brand': brand.pk})
                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response['Location'],
                    REVIEWED_BRAND_LANDING_PATHS[name],
                )

    def test_empty_select_values_do_not_block_brand_redirect(self):
        brand = self.reviewed['changan']
        response = self.client.get('/', {
            'country': '',
            'brand': brand.pk,
            'model': '',
            'category': '',
        })

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/avtozapchasti/changan/')

    def test_all_flag_can_redirect_to_reviewed_brand_landing(self):
        brand = self.reviewed['haval']
        response = self.client.get('/', {'brand': brand.pk, 'all': '1'})

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/avtozapchasti/haval/')

    def test_content_changing_filter_keeps_normal_catalog_request(self):
        brand = self.reviewed['chery']
        response = self.client.get('/', {'brand': brand.pk, 'q': 'filter'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')

    def test_unreviewed_brand_keeps_normal_catalog_request(self):
        response = self.client.get('/', {'brand': self.other.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, follow')

    def test_legacy_market_mount_also_redirects_to_canonical_landing(self):
        brand = self.reviewed['zeekr']
        response = self.client.get('/market/', {'brand': brand.pk})

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/avtozapchasti/zeekr/')
