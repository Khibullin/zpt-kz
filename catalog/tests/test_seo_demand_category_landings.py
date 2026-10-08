from django.test import TestCase

from catalog.models import Brand, Category, Country, Product
from catalog.seo_category_landings import SEO_CATEGORY_LANDINGS


class SeoDemandCategoryLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='SEO Demand Category Country')
        cls.brand = Brand.objects.create(country=country, name='Toyota')
        cls.categories = {
            name: Category.objects.create(name=name)
            for name in [
                'Кузов',
                'Двигатель',
                'Тормозная система',
                'Ходовая часть',
            ]
        }
        cls.products = {}
        for index, (slug, spec) in enumerate(SEO_CATEGORY_LANDINGS.items(), start=1):
            product = Product.objects.create(
                title=f'SEO {spec["category_name"]} TEST {index}',
                slug=f'seo-demand-category-{slug}',
                article=f'SEO-CAT-{index}',
                price=1000 + index,
                status='active',
                brand=cls.brand,
                category=cls.categories[spec['category_name']],
                seller_name='SEO Test Seller',
                whatsapp_number='+77010000000',
                description='Тестовый товар для SEO-посадочной категории.',
            )
            cls.products[slug] = product

    def test_landings_are_indexable_canonical_and_show_matching_category(self):
        for slug, spec in SEO_CATEGORY_LANDINGS.items():
            with self.subTest(slug=slug):
                response = self.client.get(f'/avtozapchasti/{slug}/')
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, spec['h1'])
                self.assertContains(
                    response,
                    '<meta name="robots" content="index, follow">',
                    html=True,
                )
                self.assertContains(
                    response,
                    (
                        '<link rel="canonical" '
                        f'href="https://zpt.kz/avtozapchasti/{slug}/">'
                    ),
                    html=True,
                )
                expected = self.products[slug]
                self.assertContains(response, expected.title)
                for other_slug, other_product in self.products.items():
                    if other_slug != slug:
                        self.assertNotContains(response, other_product.title)

    def test_static_sitemap_contains_all_demand_category_landings(self):
        response = self.client.get('/sitemap-static.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        for slug in SEO_CATEGORY_LANDINGS:
            self.assertIn(
                f'https://zpt.kz/avtozapchasti/{slug}/',
                body,
            )

    def test_brand_landing_route_remains_available(self):
        response = self.client.get('/avtozapchasti/toyota/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Автозапчасти Toyota в Казахстане')
