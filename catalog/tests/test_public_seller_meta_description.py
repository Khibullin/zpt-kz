from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import SellerProfile
from catalog.templatetags.seller_extras import public_seller_meta_description


class PublicSellerMetaDescriptionUnitTests(SimpleTestCase):
    def test_seller_description_uses_name_and_city(self):
        seller = SimpleNamespace(name='AG Parts', city='Алматы')

        description = public_seller_meta_description(seller)

        self.assertIn('AG Parts', description)
        self.assertIn('в Алматы', description)
        self.assertIn('Каталог товаров, контакты', description)
        self.assertLessEqual(len(description), 160)

    def test_seller_without_city_uses_kazakhstan(self):
        seller = SimpleNamespace(name='Test Parts', city='')

        description = public_seller_meta_description(seller)

        self.assertIn('в Казахстане', description)
        self.assertLessEqual(len(description), 160)


@override_settings(PUBLIC_BASE_URL='https://zpt.kz')
class PublicSellerMetaDescriptionIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username='seller-meta-test')
        cls.seller = SellerProfile.objects.create(
            user=user,
            name='AG Parts SEO Test',
            slug='ag-parts-seo-test',
            phone='+77713607040',
            city='Алматы',
        )

    def test_public_seller_page_has_unique_meta_and_open_graph_copy(self):
        response = self.client.get('/seller/ag-parts-seo-test/')
        expected = public_seller_meta_description(self.seller)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<meta name="description" content="{expected}">',
            html=True,
        )
        self.assertContains(
            response,
            '<meta property="og:title" content="AG Parts SEO Test — продавец автозапчастей | ZPT.KZ">',
            html=True,
        )
        self.assertContains(
            response,
            f'<meta property="og:description" content="{expected}">',
            html=True,
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://zpt.kz/seller/ag-parts-seo-test/">',
            html=True,
        )
