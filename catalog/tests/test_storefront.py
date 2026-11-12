from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase
from catalog.models import Country, Brand, CarModel, Category, Product, SellerProfile, ProductFitment

class StorefrontTests(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches["storefront_facets"].clear()
        caches["storefront_common"].clear()
        caches["seo_sitemaps"].clear()
        from catalog.storefront import refresh_home_facets
        refresh_home_facets(force=True)

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

    def test_vehicle_counts_deduplicate_multiple_paths(self):
        self.product.selected_brands.add(self.brand)
        self.product.selected_models.add(self.model)
        r = self.client.get('/')
        self.assertEqual(r.context['facets']['brand'][0]['count'], 1)
        self.assertEqual(r.context['facets']['country'][0]['count'], 1)

    def test_large_catalogue_is_counted_without_loading_all_cards(self):
        Product.objects.bulk_create([Product(title=f'Массовый товар {i}', article=f'BULK-{i}',
            seller_profile=self.seller, brand=self.brand, category=self.category, price=1300, stock_qty=2)
            for i in range(1500)], batch_size=200)
        from catalog.storefront import refresh_home_facets
        refresh_home_facets(force=True)
        r = self.client.get('/')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.context['result_count'],1501)
        self.assertEqual(len(r.context['products']),24)
        self.assertEqual(r.context['facets']['brand'][0]['count'],1501)

    def test_head_probe_does_not_load_the_catalogue(self):
        with self.assertNumQueries(0):
            r = self.client.head('/')
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.streaming)
        self.assertEqual(b''.join(r.streaming_content), b'')

    def test_default_photos_lead_but_explicit_sort_and_all_products_remain(self):
        photo=Product.objects.create(title='Я — товар с фото', main_image='products/photo.webp',
            price=5000, seller_profile=self.seller, stock_qty=2)
        from catalog.storefront import refresh_home_facets
        refresh_home_facets(force=True)  # Existing consumer publishes the next home snapshot.
        r=self.client.get('/')
        self.assertEqual(r.context['result_count'],2)
        self.assertEqual([p.pk for p in r.context['products']],[photo.pk,self.product.pk])
        self.assertContains(r,'Сначала с фото')
        for sort in ('title','price'):
            r=self.client.get('/',{'sort':sort})
            self.assertEqual(r.context['products'][0].pk,self.product.pk)

    def test_supplier_badge_uses_customer_copy(self):
        self.product.supplier='procurement'
        self.product.save()
        r=self.client.get('/')
        self.assertContains(r,'Доступно к заказу')
        self.assertNotContains(r,'По прайсу поставщика')

    def test_photos_rotate_categories_across_pages_without_losing_products(self):
        brakes=Category.objects.create(name='Тормоза')
        suspension=Category.objects.create(name='Подвеска')
        photos=[]
        for category,prefix in ((self.category,'А'),(brakes,'Б'),(suspension,'В')):
            for i in range(10):
                photos.append(Product.objects.create(title=f'{prefix} деталь {i:02}',
                    main_image='products/test.webp',category=category,
                    seller_profile=self.seller,price=1000+i,stock_qty=2))
        from catalog.storefront import refresh_home_facets
        refresh_home_facets(force=True)
        first=self.client.get('/').context['products']
        self.assertEqual(len({p.category_id for p in list(first)[:3]}),3)
        self.assertTrue(all(p.main_image for p in first))
        second=self.client.get('/',{'page':2}).context['products']
        ids=[p.pk for p in first]+[p.pk for p in second]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(set(ids),{p.pk for p in photos}|{self.product.pk})
        # Every group of three photographed products continues the rotation.
        ordered=list(first)+list(second)
        for start in range(0,30,3):
            self.assertEqual(len({p.category_id for p in ordered[start:start+3]}),3)
        self.assertEqual(ordered[-1].pk,self.product.pk)
        explicit=self.client.get('/',{'sort':'title'}).context['products']
        self.assertEqual([p.pk for p in list(explicit)[:3]],[p.pk for p in photos[:3]])
