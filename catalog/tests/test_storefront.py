from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase
from catalog.models import Country, Brand, CarModel, Category, Product, SellerProfile, ProductFitment

class StorefrontTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller=SellerProfile.objects.create(user=User.objects.create(username='retail'),name='AG Parts',slug='ag-parts')
        cls.country=Country.objects.create(name='Китай')
        cls.brand=Brand.objects.create(name='Chery',country=cls.country)
        cls.model=CarModel.objects.create(name='Tiggo 7 Pro',brand=cls.brand)
        cls.category=Category.objects.create(name='Фильтры')
        cls.product=Product.objects.create(title='Фильтр масляный',article='AG-101',price=2500,stock_qty=5,seller_profile=cls.seller,brand=cls.brand,car_model=cls.model,category=cls.category)
        Product.objects.create(title='Чужая деталь',price=1000,seller_name='Другой продавец')
    def test_home_only_our_products(self):
        r=self.client.get('/')
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'AG-101')
        self.assertNotContains(r,'Чужая деталь')
        self.assertContains(r,'class="retail-row"')
        self.assertNotContains(r,'Зарегистрироваться продавцом')
    def test_selection_counts(self):
        r=self.client.get('/',{'brand':self.brand.pk,'model':self.model.pk})
        self.assertEqual(r.context['result_count'],1)
        self.assertEqual(r.context['facets']['brand'][0]['count'],1)
    def test_invalid_ids_fail_closed(self):
        for key in ('model','brand','country','category','year'):
            r=self.client.get('/',{key:'not-an-id'})
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.context['result_count'],0)
    def test_joint_year_engine(self):
        self.product.fitment_reviewed_on=date(2026,10,29);self.product.save()
        for engine,start,end in [('E1',2021,2023),('E2',2024,2025)]:
            ProductFitment.objects.create(product=self.product,car_model=self.model,status='confirmed',engine_policy='required',engine_code=engine,year_from=start,year_to=end,source_url='https://example.com/catalog',verified_on=date(2026,10,29))
        r=self.client.get('/',{'model':self.model.pk,'year':'2023','engine':'E1'})
        self.assertEqual(r.context['result_count'],1)
        r=self.client.get('/',{'model':self.model.pk,'year':'2023','engine':'E2'})
        self.assertEqual(r.context['result_count'],0)
    def test_stock_and_article_search(self):
        self.assertEqual(self.client.get('/',{'q':'AG-101'}).context['result_count'],1)
        self.product.stock_qty=0;self.product.save()
        self.assertEqual(self.client.get('/',{'stock':'in','q':'AG-101'}).context['result_count'],0)
    def test_sort_pagination(self):
        for i in range(25):Product.objects.create(title=f'Фильтр {i}',article=f'TEST-{i}',price=100+i,seller_profile=self.seller,brand=self.brand)
        r=self.client.get('/',{'brand':self.brand.pk,'sort':'price'})
        self.assertEqual(r.context['products'][0].price,100)
        self.assertIn('brand=',r.context['next_url']);self.assertIn('sort=price',r.context['next_url'])

    def test_picker_brand_link_stays_in_storefront(self):
        r=self.client.get('/',{'brand':self.brand.pk,'picker':'1'})
        self.assertEqual(r.status_code,200)
        self.assertTemplateUsed(r,'catalog/interface/storefront.html')
        self.assertContains(r,'picker=1')
