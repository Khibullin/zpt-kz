from django.test import TestCase
from core.models import Seller, Brand, Country, PartCategory, SellerWorkInterval
from datetime import time


class PartsDirectoryDesignTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.country = Country.objects.create(name='QA country')
        cls.brand = Brand.objects.create(name='QA brand', country=cls.country)
        cls.category = PartCategory.objects.create(name='QA category')
        cls.store = Seller.objects.create(name='Parts Store', sells_new_parts=True, city='Алматы', transport_type='car',
            whatsapp='87012223344', address='Адрес магазина, 1', market_location='Адрес магазина, 1',
            directory_description='Публичное описание', notes='PRIVATE INTERNAL NOTE', latitude='43.25', longitude='76.95')
        cls.store.selected_brands.add(cls.brand)
        cls.store.selected_categories.add(cls.category)
        cls.mixed = Seller.objects.create(name='Mixed Store', sells_new_parts=True, is_dismantler=True, city='Астана', transport_type='car')
        for name, extra in [('Unknown', {}), ('Used only', {'sells_new_parts': False, 'is_dismantler':True}),
                            ('Test', {'sells_new_parts':True, 'is_test_seller':True}),
                            ('Paused', {'sells_new_parts':True, 'is_paused':True}),
                            ('Inactive', {'sells_new_parts':True, 'is_active':False})]:
            Seller.objects.create(name=name,city='Костанай',transport_type='car', **extra)

    def test_public_scope_and_mixed_membership(self):
        page = self.client.get('/parts-sellers/')
        self.assertTemplateUsed(page,'catalog/interface/parts_sellers/catalog.html')
        self.assertEqual(set(s.pk for s in page.context['sellers']),{self.store.pk,self.mixed.pk})
        self.assertEqual(set(page.context['cities']),{'Алматы','Астана'})
        self.assertContains(page,f'/parts-seller/{self.mixed.pk}/?directory=parts')
        self.assertNotContains(page,'PRIVATE INTERNAL NOTE')
        self.assertContains(self.client.get('/auto-dismantlers/'),'Mixed Store')
        self.assertEqual(Seller.objects.get(pk=self.store.pk).notes,'PRIVATE INTERNAL NOTE')

    def test_filters_use_explicit_specializations_and_public_search(self):
        self.mixed.all_brands=True
        self.mixed.save(update_fields=['all_brands'])
        page=self.client.get('/parts-sellers/',{'brand':self.brand.pk,'category':self.category.pk,'city':'Алматы'})
        self.assertEqual([s.pk for s in page.context['sellers']],[self.store.pk])
        page=self.client.get('/parts-sellers/',{'brand':self.brand.pk})
        self.assertEqual([s.pk for s in page.context['sellers']],[self.store.pk])
        for q in ['Parts Store','Адрес магазина','Публичное описание']:
            self.assertEqual(self.client.get('/parts-sellers/',{'q':q}).context['page_obj'].paginator.count,1)
        self.assertEqual(self.client.get('/parts-sellers/',{'q':'PRIVATE INTERNAL NOTE'}).context['page_obj'].paginator.count,0)
        self.assertEqual(self.client.get('/parts-sellers/',{'city':'Unknown city'}).context['page_obj'].paginator.count,0)

    def test_profile_contacts_schedule_routes_and_empty_fields(self):
        SellerWorkInterval.objects.create(seller=self.store,weekday=0,opens=time(9),closes=time(18))
        page=self.client.get(f'/parts-seller/{self.store.pk}/')
        self.assertTemplateUsed(page,'catalog/interface/parts_sellers/detail.html')
        for text in ['tel:+77012223344','wa.me/77012223344','09:00–18:00','Построить маршрут','Публичное описание']:
            self.assertContains(page,text)
        # JSON-LD legitimately repeats the public street address in <head>.
        # The visible organization card must still display it exactly once.
        visible_card = page.content.decode('utf-8').split('<div class="org-detail">', 1)[1]
        self.assertEqual(visible_card.count('Адрес магазина, 1'), 1)
        self.assertNotContains(page,'PRIVATE INTERNAL NOTE')
        page=self.client.get(f'/parts-seller/{self.mixed.pk}/?directory=parts')
        self.assertTemplateUsed(page,'catalog/interface/parts_sellers/detail.html')
        self.assertNotContains(page,'Адрес и режим работы')
        self.assertNotContains(page,'Связаться с организацией')
        self.assertTemplateUsed(self.client.get(f'/parts-seller/{self.mixed.pk}/'),'catalog/interface/dismantlers/detail.html')

    def test_pagination_preserves_filters(self):
        Seller.objects.bulk_create([Seller(name=f'Page seller {i}',city='Алматы',transport_type='car',sells_new_parts=True) for i in range(23)])
        page=self.client.get('/parts-sellers/',{'q':'Page seller','city':'Алматы'})
        self.assertEqual(len(page.context['sellers']),20)
        self.assertContains(page,'page=2')
        second=self.client.get('/parts-sellers/',{'q':'Page seller','city':'Алматы','page':2})
        self.assertEqual(len(second.context['sellers']),3)
        self.assertFalse(set(s.pk for s in page.context['sellers']) & set(s.pk for s in second.context['sellers']))
