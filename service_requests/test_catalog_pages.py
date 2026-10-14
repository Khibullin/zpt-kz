from django.test import TestCase
from .models import Service, ServiceSeller

class CatalogPagesTests(TestCase):
    def make(self,name,phone,services=(),**kwargs):
        obj=ServiceSeller.objects.create(name=name,whatsapp=phone,city='Алматы',seller_type='sto',**kwargs)
        for value in services:
            obj.services.add(Service.objects.get_or_create(name=value)[0])
        return obj

    def test_scope_and_unknown_and_no_mutation(self):
        repair=self.make('Workshop','77000000001',['Диагностика'])
        detail=self.make('Studio','77000000002',['Полировка'])
        mixed=self.make('Both','77000000003',['Полировка','Диагностика'])
        unknown=self.make('Unknown','77000000004',catalog_only=True,receive_requests=False)
        self.make('Inactive','77000000005',is_active=False)
        for kind,wanted,excluded in [('sto',repair,detail),('detailing',detail,repair)]:
            response=self.client.get('/catalog/services/',{'type':kind})
            self.assertEqual(response.status_code,200)
            ids={c['seller'].pk for c in response.context['sellers']}
            self.assertEqual(ids,{wanted.pk,mixed.pk,unknown.pk})
            self.assertNotIn(excluded.pk,ids)
            self.assertNotContains(response,'Источники и контакты')
        unknown.refresh_from_db(); self.assertFalse(unknown.receive_requests)
        self.assertEqual(unknown.seller_type,'sto')

    def test_filters_and_legacy_service(self):
        self.make('Garage','77000000006',['Диагностика'])
        self.make('Unknown','77000000007')
        for params in [{'type':'sto','category':'diagnostics'},{'type':'sto','service':'Диагностика'}]:
            r=self.client.get('/catalog/services/',params)
            self.assertEqual(len(r.context['sellers']),1)
        self.assertEqual(len(self.client.get('/catalog/services/',{'city':'invalid'}).context['sellers']),0)

    def test_landings_and_empty(self):
        for section in ('repair','detailing'):
            r=self.client.get('/sections/'+section+'/')
            self.assertEqual(r.status_code,200)
            self.assertContains(r,'service-tiles')
            self.assertContains(r,'Исполнители пока не найдены')
            self.assertNotContains(r,'Пример сервиса')
            self.assertNotContains(r,'Демонстрационные карточки')

    def test_pagination_keeps_filters_and_has_no_duplicates(self):
        for i in range(14): self.make('Garage '+str(i),f'77000001{i:03d}',['Диагностика'])
        a=self.client.get('/catalog/services/',{'type':'sto','city':'Алматы','q':'Garage'})
        b=self.client.get('/catalog/services/',{'type':'sto','city':'Алматы','q':'Garage','page':2})
        self.assertEqual(len(a.context['sellers']),12)
        self.assertEqual(len(b.context['sellers']),2)
        self.assertIn('type=sto',a.context['sellers'].next_url)
        self.assertFalse({c['seller'].pk for c in a.context['sellers']} & {c['seller'].pk for c in b.context['sellers']})
