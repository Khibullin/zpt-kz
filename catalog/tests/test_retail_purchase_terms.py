from types import SimpleNamespace
from django.test import SimpleTestCase, RequestFactory
from django.template.loader import render_to_string
from core.seo import seo_context


class RetailPurchaseTermsTests(SimpleTestCase):
    def product(self, supplier='omega', url=''):
        listing=SimpleNamespace(is_active=True,public_url=url)
        return SimpleNamespace(supplier=supplier,kaspi_listings=SimpleNamespace(all=lambda:[listing] if url else []))

    def test_supplier_stock_is_not_presented_as_own_and_no_payment_before_confirmation(self):
        html=render_to_string('catalog/includes/purchase_terms.html',{'product':self.product()})
        self.assertIn('это не остаток на нашем складе',html)
        self.assertIn('Не оплачивайте заказ до подтверждения',html)
        self.assertIn('data-kk=',html)
        self.assertIn('/contacts/',html)

    def test_kaspi_purchase_keeps_its_own_checkout_terms(self):
        html=render_to_string('catalog/includes/purchase_terms.html',{'product':self.product('local','https://kaspi.kz/shop/p/filter-123456/')})
        self.assertIn('Покупка оформляется на Kaspi',html)
        self.assertNotIn('Не оплачивайте заказ до подтверждения',html)

    def test_unknown_own_stock_does_not_gain_supplier_claim(self):
        html=render_to_string('catalog/includes/purchase_terms.html',{'product':self.product('local')})
        self.assertNotIn('Товар у поставщика',html)
        self.assertIn('Стоимость и срок доставки',html)

    def test_new_filter_variants_remain_out_of_search_index(self):
        factory=RequestFactory()
        for query in ('year=2020','engine=2.0','maker=EEP','subgroup=Фильтры','stock=1','picker=1'):
            with self.subTest(query=query):
                context=seo_context(factory.get('/?'+query))
                self.assertEqual(context['seo_robots'],'noindex, follow')
                self.assertEqual(context['seo_canonical_url'],'https://zpt.kz/')
