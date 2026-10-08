from django.test import TestCase

from catalog.article_utils import filter_products_by_public_query
from catalog.models import Product


class PublicExactArticleSearchTests(TestCase):
    def _product(self, *, title, article='', oem=''):
        return Product.objects.create(
            title=title,
            article=article,
            oem_cross_references=oem,
            seller_name='Test Seller',
            whatsapp_number='77015550000',
            status='active',
            price_on_request=True,
        )

    def test_exact_article_still_matches_product_article(self):
        expected = self._product(
            title='Exact article',
            article='M11-1109111',
        )
        self._product(
            title='Other',
            article='M11-1109112',
        )

        qs = filter_products_by_public_query(
            Product.objects.filter(status='active'),
            'M111109111',
        )

        self.assertEqual(list(qs.values_list('pk', flat=True)), [expected.pk])

    def test_exact_query_matches_oem_reference_with_different_punctuation(self):
        expected = self._product(
            title='OEM match',
            article='SELLER-SKU-1',
            oem='F4J16-3707010\nALT-001',
        )

        qs = filter_products_by_public_query(
            Product.objects.filter(status='active'),
            'F4J163707010',
        )

        self.assertEqual(list(qs.values_list('pk', flat=True)), [expected.pk])

    def test_exact_oem_search_does_not_match_longer_partial_number(self):
        self._product(
            title='Longer OEM',
            article='SELLER-SKU-2',
            oem='M11-11091110',
        )

        qs = filter_products_by_public_query(
            Product.objects.filter(status='active'),
            'M11-1109111',
        )

        self.assertFalse(qs.exists())

    def test_regular_text_search_keeps_description_and_compatibility_behavior(self):
        description_match = self._product(
            title='Generic product',
            article='SKU-3',
        )
        description_match.description = 'Фильтр для суровых условий эксплуатации'
        description_match.save(update_fields=['description'])

        compatibility_match = self._product(
            title='Another product',
            article='SKU-4',
        )
        compatibility_match.compatibility = 'Toyota Camry 50'
        compatibility_match.save(update_fields=['compatibility'])

        qs = filter_products_by_public_query(
            Product.objects.filter(status='active'),
            'Camry',
        )

        self.assertEqual(
            set(qs.values_list('pk', flat=True)),
            {compatibility_match.pk},
        )
