from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from catalog.models import Product
from catalog.templatetags.product_extras import is_public_product_detail_request


class NumericProductUrlTests(TestCase):
    def make_product(self, **overrides):
        data = {
            'title': 'Test product',
            'slug': 'test-product',
            'article': '900000001',
            'seller_name': 'Test seller',
            'whatsapp_number': '+77000000000',
            'status': 'active',
        }
        data.update(overrides)
        return Product.objects.create(**data)

    def test_numeric_slug_is_served_instead_of_becoming_false_pk_404(self):
        self.make_product(slug='2032047000', article='2032047000')

        with patch('catalog.views.product_detail', return_value=HttpResponse('product')) as detail:
            response = self.client.get('/2032047000/')

        self.assertEqual(response.status_code, 200)
        detail.assert_called_once()
        self.assertEqual(detail.call_args.kwargs['slug'], '2032047000')

    def test_numeric_route_is_recognized_as_public_product_detail_for_seo(self):
        request = RequestFactory().get('/2032047000/')
        request.resolver_match = resolve('/2032047000/')
        self.assertTrue(is_public_product_detail_request(request))

    def test_exact_numeric_article_redirects_permanently_to_product_canonical(self):
        self.make_product(slug='general-motors-filter', article='25181616')

        response = self.client.get('/25181616/')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/general-motors-filter/')

    def test_numeric_article_preserves_leading_zeroes(self):
        self.make_product(slug='leading-zero-product', article='0012345678')

        response = self.client.get('/0012345678/')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/leading-zero-product/')

    def test_existing_legacy_pk_redirects_permanently_to_slug(self):
        product = self.make_product(slug='legacy-pk-product', article='987654321')

        response = self.client.get(f'/{product.pk}/')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/legacy-pk-product/')

    def test_pk_equal_to_article_without_slug_does_not_self_redirect(self):
        product = self.make_product(slug='', article='temporary')
        product.article = str(product.pk)
        product.save(update_fields=['article'])

        with patch('catalog.views.product_detail', return_value=HttpResponse('product')) as detail:
            response = self.client.get(f'/{product.pk}/')

        self.assertEqual(response.status_code, 200)
        detail.assert_called_once()
        self.assertEqual(detail.call_args.kwargs['pk'], product.pk)

    def test_duplicate_numeric_article_goes_to_search_without_guessing(self):
        self.make_product(slug='seller-one-product', article='7777777777')
        self.make_product(slug='seller-two-product', article='7777777777')

        response = self.client.get('/7777777777/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/?q=7777777777')

    def test_root_favicon_redirects_to_existing_static_asset(self):
        response = self.client.get('/favicon.ico')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/static/images/favicon.ico')

    @override_settings(DEBUG=False)
    def test_unknown_numeric_url_returns_branded_noindex_404(self):
        response = self.client.get('/999999999999/')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Страница не найдена', status_code=404)
        self.assertContains(response, 'Оставить заявку', status_code=404)
        self.assertContains(
            response,
            '<meta name="robots" content="noindex, follow">',
            status_code=404,
            html=True,
        )

    @override_settings(DEBUG=False)
    def test_unknown_slug_also_uses_branded_404(self):
        response = self.client.get('/this-product-does-not-exist/')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Страница не найдена', status_code=404)
