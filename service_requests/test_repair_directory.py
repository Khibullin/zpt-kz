from django.test import TestCase
from .models import ServiceSeller, Service

class RepairDirectoryTests(TestCase):
    def make(self,name,phone,**kw):
        return ServiceSeller.objects.create(name=name,whatsapp=phone,city='Алматы',seller_type='sto',**kw)

    def test_main_url_uses_approved_compact_directory(self):
        self.make('Проверенная СТО', '77000000001')
        page = self.client.get('/catalog/services/')
        self.assertTemplateUsed(page, 'catalog/interface/services/repair_catalog.html')
        self.assertContains(page, 'directory-card')
        self.assertContains(page, 'Фильтры')
        self.assertNotContains(page, 'Выберите направление')
        self.assertNotContains(page, 'Иллюстрация')
        self.assertNotContains(page, 'Найдите сервис для вашего автомобиля')

    def test_vehicle_filters_use_known_evidence_and_do_not_write(self):
        known = self.make('Мастерская', '77000000002', directory_transport='Легковые автомобили', directory_brands='Toyota, Lexus', catalog_only=True, receive_requests=False)
        self.make('Неизвестная специализация', '77000000003')
        truck = self.make('Грузовая мастерская', '77000000004')
        truck.services.add(Service.objects.create(name='Грузовой автосервис'))
        for params, wanted in [({'transport_type':'car', 'brand':'Toyota'}, known), ({'transport_type':'truck'}, truck)]:
            page = self.client.get('/catalog/services/', params)
            self.assertEqual([c['seller'].pk for c in page.context['sellers']], [wanted.pk])
        page = self.client.get('/catalog/services/', {'brand':'Toyota', 'model':'Camry'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)
        known.refresh_from_db()
        self.assertFalse(known.receive_requests)
        self.assertIsNone(known.request_filters)

    def test_confirmed_models_are_filtered_and_pagination_keeps_selection(self):
        snapshot = {'transport_types':['car'], 'all_brands':False, 'all_categories':True, 'categories':[], 'service_type':'sto',
                    'brands':[{'id':1,'name':'Toyota','transport_type':'car','all_models':False,'models':[{'id':1,'name':'Camry'}]}]}
        for i in range(21):
            self.make(f'Автосервис {i:02}', f'77080000{i:03}', request_filters=snapshot)
        params = {'transport_type':'car','brand':'Toyota','model':'Camry'}
        page = self.client.get('/catalog/services/', params)
        self.assertEqual(page.context['page_obj'].paginator.count, 21)
        self.assertContains(page, 'brand=Toyota')
        self.assertContains(page, 'model=Camry')
        self.assertContains(page, 'page=2')
        self.assertEqual(page.context['model_options'], ['Camry'])
        self.assertEqual(self.client.get('/catalog/services/', {**params,'model':'Corolla'}).context['page_obj'].paginator.count, 0)

    def test_scope_and_unavailable_records(self):
        good=self.make('Ремонт','87012223344',catalog_notes='PRIVATE NOTE')
        for i,flags in enumerate([{'is_active':False},{'is_paused':True},{'is_test_seller':True}]):
            self.make('Hidden '+str(i),'7700000000'+str(i),**flags)
        detail=ServiceSeller.objects.create(name='Detail studio',whatsapp='77000000999',city='Астана',seller_type='detailing')
        page=self.client.get('/catalog/services/',{'type':'sto'})
        self.assertEqual([c['seller'].pk for c in page.context['sellers']],[good.pk])
        self.assertEqual(page.context['cities'],['Алматы'])
        self.assertContains(page,'tel:+77012223344')
        self.assertContains(page,'wa.me/77012223344')
        self.assertNotContains(page,'PRIVATE NOTE')
        self.assertTemplateUsed(self.client.get('/catalog/services/?type=detailing'),'catalog/interface/services/repair_catalog.html')
        self.assertTemplateUsed(self.client.get(f'/catalog/services/{detail.pk}/'),'catalog/interface/services/repair_detail.html')

    def test_profile_only_stored_values_and_safe_links(self):
        s=self.make('СТО', '87012223344', address='Улица, 1',working_hours='Пн–Пт 09:00–18:00',map_link='https://2gis.kz/almaty/firm/123',instagram='https://instagram.com/example',website='javascript:alert(1)',catalog_notes='PRIVATE NOTE')
        s.services.add(Service.objects.create(name='Диагностика'))
        page=self.client.get(f'/catalog/services/{s.pk}/?directory=sto')
        self.assertTemplateUsed(page,'catalog/interface/services/repair_detail.html')
        for value in ['Улица, 1','Пн–Пт 09:00–18:00','Открыть на карте','https://2gis.kz/almaty/firm/123','Диагностика','tel:+77012223344']:
            self.assertContains(page,value)
        for value in ['javascript:','PRIVATE NOTE','★','Построить маршрут']:
            self.assertNotContains(page,value)
        s.refresh_from_db();self.assertEqual(s.catalog_notes,'PRIVATE NOTE')

    def test_empty_profile_and_filters(self):
        s=self.make('Пустая СТО','invalid')
        page=self.client.get(f'/catalog/services/{s.pk}/')
        self.assertNotContains(page,'Адрес и режим работы')
        self.assertNotContains(page,'Связаться с организацией')
        s2=self.make('Диагностика двигателя','77012223345',district='Центральный',address='Тестовая улица')
        s2.services.add(Service.objects.create(name='Диагностика'))
        page=self.client.get('/catalog/services/',{'type':'sto','city':'Алматы','district':'Центральный','service':'Диагностика','q':'Тестовая','category':'diagnostics'})
        self.assertEqual([c['seller'].pk for c in page.context['sellers']],[s2.pk])
        self.assertEqual(self.client.get('/catalog/services/?type=sto&city=invalid').context['page_obj'].paginator.count,0)

    def test_detailing_filters_profiles_and_mixed_navigation(self):
        mixed=self.make('Ремонт и полировка', '77010000001', address='Абая, 10', district='Центр')
        mixed.services.add(Service.objects.create(name='Полировка'))
        self.make('Обычная станция', '77010000002')
        self.make('Полировка скрыта', '77010000003', is_paused=True)
        page=self.client.get('/catalog/services/', {'type':'detailing','category':'polish','city':'Алматы','service':'Полировка','district':'Центр'})
        self.assertEqual([c['seller'].pk for c in page.context['sellers']], [mixed.pk])
        self.assertContains(page, 'Каталог детейлинга и тюнинга')
        self.assertContains(page, '?directory=detailing')
        self.assertContains(page, 'value="detailing"')
        self.assertNotContains(page, 'Добавить СТО')
        for kind,title in [('sto','Назад в каталог СТО'),('detailing','Назад в каталог детейлинга и тюнинга')]:
            profile=self.client.get(f'/catalog/services/{mixed.pk}/', {'directory':kind})
            self.assertContains(profile,title)
            self.assertNotContains(profile,'★')
        mixed.refresh_from_db()
        self.assertEqual(mixed.seller_type, 'sto')

    def test_detailing_pagination_and_unavailable_profiles(self):
        for i in range(21):
            self.make(f'Тюнинг {i:02}', f'77020000{i:03}')
        page=self.client.get('/catalog/services/?type=detailing&q=Тюнинг')
        self.assertEqual(len(page.context['sellers']),20)
        self.assertContains(page,'type=detailing&amp;q=')
        self.assertContains(page,'page=2')
        hidden=self.make('Тюнинг тестовый','77030000001',is_test_seller=True)
        self.assertEqual(self.client.get(f'/catalog/services/{hidden.pk}/?directory=detailing').status_code,404)
