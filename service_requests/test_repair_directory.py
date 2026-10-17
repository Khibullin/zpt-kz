from django.test import TestCase
from .models import ServiceSeller, Service

class RepairDirectoryTests(TestCase):
    def make(self,name,phone,**kw):
        return ServiceSeller.objects.create(name=name,whatsapp=phone,city='Алматы',seller_type='sto',**kw)

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
        self.assertTemplateUsed(self.client.get('/catalog/services/?type=detailing'),'catalog/interface/services/page.html')
        self.assertTemplateUsed(self.client.get(f'/catalog/services/{detail.pk}/'),'catalog/services/detail.html')

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
