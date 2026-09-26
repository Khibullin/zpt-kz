from django.test import TestCase

from catalog.ag_parts_fitment_audit import apply_fitment_plans, load_batch, plan_fitment_batch
from catalog.models import Product


class AgPartsFitmentBatch18Tests(TestCase):
    def test_only_four_exact_articles_and_commercial_fields_unchanged(self):
        articles = ('151000151AA', 'EM2E-8121211E', '8890649934', 'CD569F2801032700')
        products = {}
        for article in articles:
            products[article] = Product.objects.create(
                article=article,
                title='Исходная карточка ' + article,
                seller_name='AG Parts',
                whatsapp_number='+77713607040',
                status='active',
                city='Алматы',
                price=4321,
                cost_price=1234,
                stock_qty=7,
                slug='test-' + article.lower(),
            )
        spec = load_batch('18')
        self.assertEqual({row['article'] for row in spec['articles']}, set(articles))
        planned = plan_fitment_batch(spec)
        self.assertEqual({plan.article for plan in planned}, set(articles))
        self.assertTrue(all(plan.status == 'WOULD_CHANGE' for plan in planned))
        apply_fitment_plans(planned, apply=True)

        for article, product in products.items():
            product.refresh_from_db()
            self.assertEqual((product.price, product.cost_price, product.stock_qty), (4321, 1234, 7))
            self.assertEqual(product.article, article)
            self.assertEqual(product.slug, 'test-' + article.lower())
            self.assertEqual(product.status, 'active')
            self.assertEqual(product.seller_name, 'AG Parts')
        self.assertIn('SQRF4J20', products['151000151AA'].engine_compatibility)
        self.assertNotIn('151000151AB', products['151000151AA'].oem_cross_references)
        self.assertIn('Atto 3', products['EM2E-8121211E'].compatibility)
        self.assertIn('Zeekr 7X', products['8890649934'].compatibility)
        self.assertIn('UNI-K', products['CD569F2801032700'].compatibility)
        self.assertEqual(Product.objects.count(), 4)
