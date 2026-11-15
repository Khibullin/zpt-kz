from django.test import TestCase, SimpleTestCase
from django.contrib.auth.models import User
from catalog.models import Product, SellerProfile
from catalog.product_description import readable_description
from catalog.applicability import build_product_applicability
from procurement.models import Supplier, Offer, RetailListing, VehicleFitment

class DescriptionTests(SimpleTestCase):
    def test_supplier_noise_does_not_destroy_source_or_codes(self):
        raw = 'Вентилятор OEM: 4A0959455C Характеристики поставщика: Марка авто: AUDI Модель: 100 показать ещё (6), A6 Кузов: C4 Применимость OEM: 4A0-959-455-C ► AUDI 100 1990–1994 ACE 1984cc'
        result = readable_description(raw)
        self.assertEqual(result['original'], raw)
        self.assertIn('4A0959455C', result['summary'])
        self.assertNotIn('показать ещё', str(result['facts']))
        self.assertIn('ACE 1984cc', str(result['compatibility']))
        self.assertIn('4A0-959-455-C', str(result['compatibility']))

class RetailContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seller = SellerProfile.objects.create(user=User.objects.create(username='retail-description'), name='AG Parts', slug='ag-parts')
        cls.product = Product.objects.create(title='Вентилятор WXQP 123',article='123',slug='fan-description-check',seller_profile=seller,status='active',description='Вентилятор <script>alert(1)</script> Характеристики поставщика: Модель: 100 Применимость ► 1990–1994')

    def test_card_escapes_source_and_does_not_show_seller_grid(self):
        response = self.client.get('/fan-description-check/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Исходное описание поставщика')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, 'Другие товары продавца')
        self.assertContains(response, '/find-part/?request=1')

    def test_supplier_table_keeps_configurations_and_unverified_status(self):
        supplier = Supplier.objects.create(name='Omega test')
        offer = Offer.objects.create(supplier=supplier,article='123',brand='WXQP',name='Вентилятор',cost=1000)
        RetailListing.objects.create(offer=offer,product=self.product)
        VehicleFitment.objects.create(offer=offer,make='Audi',model='100',source_url='https://example.com/123',verified=False,configurations=[{'years':'1990–1994','engine':'ACE','chassis':'C4','capacity':'1984cc'}])
        result = build_product_applicability(self.product)
        self.assertTrue(result['has_block'])
        row = result['fitment_rows'][0]
        self.assertEqual(row['period'], '1990–1994')
        self.assertEqual(row['engine'], 'ACE')
        self.assertTrue(row['needs_check'])
        self.assertIn('1984cc', row['note'])
