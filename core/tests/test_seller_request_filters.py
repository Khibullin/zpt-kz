import json
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from core.models import Country, Brand, CarModel, PartCategory, Seller, Request, BroadcastSettings, RequestDispatch
from core.services.seller_request_filters import save_filters, matches_request, initial_filters
from core.services.seller_request_access import create_seller_request_access
from core.request_dispatch_service import seller_allowed_for_dispatch, send_single_dispatch
from core.templatetags.dismantler_detail import seller_specializations
from core.views import _find_matching_sellers, _build_dispatch_queue


class SellerRequestFilterTests(TestCase):
    def setUp(self):
        country=Country.objects.create(name='Япония')
        self.toyota=Brand.objects.create(name='Toyota',country=country,transport_type='car')
        self.lexus=Brand.objects.create(name='Lexus',country=country,transport_type='car')
        self.camry=CarModel.objects.create(name='Camry',brand=self.toyota,transport_type='car')
        self.rav=CarModel.objects.create(name='RAV4',brand=self.toyota,transport_type='car')
        self.rx=CarModel.objects.create(name='RX',brand=self.lexus,transport_type='car')
        self.optics=PartCategory.objects.create(name='Оптика')
        self.seller=Seller.objects.create(name='Магазин',password_hash='existing-registration-hash',whatsapp='77011234567',transport_type='car',city='Алматы',receive_requests=True,sells_new_parts=True,all_categories=True,all_brands=True,all_models=True,all_countries=True)
        self.req=Request.objects.create(transport_type='car',brand='Toyota',model='Camry',category='Оптика',city='Алматы',phone='77011112233',source=Request.SOURCE_HOME_SHORT,search_scope='city')
        self.access=create_seller_request_access(request=self.req,seller=self.seller)
        self.url=reverse('seller_request_filters_link',kwargs={'token':self.access.token})
        self.settings=BroadcastSettings.load();self.settings.mode='live';self.settings.emergency_stop=False;self.settings.save()
        self.data=dict(transport_types=['car'],all_brands=False,all_categories=False,category_ids=[self.optics.pk],brands=[dict(id=self.toyota.pk,all_models=False,model_ids=[self.camry.pk]),dict(id=self.lexus.pk,all_models=True,model_ids=[])])

    def save(self):
        self.seller=save_filters(self.seller,self.data)

    def test_atomic_save_syncs_profile_without_changing_dispatch_controls(self):
        self.save();self.assertTrue(self.seller.receive_requests);self.assertFalse(self.seller.is_dismantler)
        self.assertEqual(self.seller.city,'Алматы')
        self.assertEqual(set(self.seller.selected_brands.values_list('pk',flat=True)),{self.toyota.pk,self.lexus.pk})
        self.assertEqual(list(self.seller.selected_models.all()),[self.camry])
        self.assertIn('Toyota: Camry',str(seller_specializations(self.seller)))
        self.assertIn('Lexus: все модели',str(seller_specializations(self.seller)))
        self.assertEqual(initial_filters(self.seller)['brands'][0]['model_ids'],[self.camry.pk])

    def test_invalid_and_cross_brand_models_do_not_change_profile(self):
        for change in [dict(category_ids=[]),dict(transport_types=[]),dict(all_brands='false'),dict(brands=[dict(id=self.toyota.pk,all_models=False,model_ids=[self.rx.pk])])]:
            with self.assertRaises(ValueError):save_filters(self.seller,{**self.data,**change})
            self.seller.refresh_from_db();self.assertIsNone(self.seller.request_filters)

    def test_strict_filters_survive_fallback_and_all_kz_mode(self):
        self.save()
        for brand,model,category,transport,expected in [('Toyota','Camry','Оптика','car',True),('Toyota','RAV4','Оптика','car',False),('Kia','Picanto','Оптика','car',False),('Lexus','RX','Оптика','car',True),('Toyota','Camry','Двигатель','car',False),('Toyota','Camry','Оптика','truck',False),('','','Оптика','car',False)]:
            for dispatch_mode in [Request.DISPATCH_MODE_MATCHED,Request.DISPATCH_MODE_ALL_KZ]:
                self.req.brand=brand;self.req.model=model;self.req.category=category;self.req.transport_type=transport;self.req.dispatch_mode=dispatch_mode
                self.assertEqual(matches_request(self.seller.request_filters,self.req),expected)
                self.assertEqual(_find_matching_sellers(self.req)[0].filter(pk=self.seller.pk).exists(),expected)
                self.assertEqual(seller_allowed_for_dispatch(self.seller,self.settings,self.req),expected)

    def test_two_transports_all_brands_and_direction_geography_stay_strict(self):
        self.seller=save_filters(self.seller,dict(transport_types=['car','truck'],all_brands=True,all_categories=True))
        self.req.transport_type='truck';self.assertTrue(_find_matching_sellers(self.req)[0].exists())
        self.req.is_dismantler_request=True;self.assertFalse(_find_matching_sellers(self.req)[0].exists())
        self.seller.is_dismantler=True;self.seller.save();self.assertTrue(_find_matching_sellers(self.req)[0].exists())
        self.req.city='Астана';self.assertFalse(_find_matching_sellers(self.req)[0].exists())
        self.assertFalse(seller_allowed_for_dispatch(self.seller,self.settings,self.req))

    def test_queue_and_direct_sender_recheck_changed_filters(self):
        dispatches=_build_dispatch_queue(self.req,[self.seller]);self.assertEqual(len(dispatches),1)
        self.data['brands']=[dict(id=self.lexus.pk,all_models=True,model_ids=[])];self.save()
        self.assertEqual(_build_dispatch_queue(self.req,[self.seller]),[])
        with patch('core.views.send_whatsapp_template') as send:
            result=send_single_dispatch(dispatches[0]);send.assert_not_called()
        self.assertEqual(result['reason'],'seller_filter_mismatch')
        dispatches[0].refresh_from_db();self.assertEqual(dispatches[0].status,RequestDispatch.STATUS_PAUSED)

    def test_link_and_cabinet_same_profile_csrf_and_expiration(self):
        client=Client(enforce_csrf_checks=True)
        page=client.get(self.url);self.assertEqual(page.status_code,200)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertContains(page, 'name="referrer" content="same-origin"')
        self.assertContains(page,'Вы ещё не настроили специализацию')
        self.assertEqual(client.post(self.url,{'filters':json.dumps(self.data)}).status_code,403)
        token=client.cookies['csrftoken'].value
        response=client.post(self.url,{'filters':json.dumps(self.data),'csrfmiddlewaretoken':token,'seller_id':'99999'})
        self.assertEqual(response.status_code,302)
        self.seller.refresh_from_db();self.assertIsNotNone(self.seller.request_filters)
        session=client.session;session['seller_id']=self.seller.pk;session.save()
        cabinet=client.get(reverse('seller_request_filters'));self.assertEqual(cabinet.context['filter_data'],initial_filters(self.seller))
        self.access.expires_at=timezone.now()-timedelta(seconds=1);self.access.save()
        self.assertEqual(client.get(self.url).status_code,410)
        self.assertEqual(client.post(self.url,{'filters':json.dumps(self.data),'csrfmiddlewaretoken':token}).status_code,410)
        self.assertEqual(client.get('/sr/not-a-real-token/filters/').status_code,404)

    def _bind_parts_cabinet(self, client, seller):
        session = client.session
        session['seller_id'] = seller.pk
        session.save()

    def _assert_https_parts_filter_save(self, seller, url, access, label):
        client = Client(enforce_csrf_checks=True)
        page = client.get(url, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertContains(page, 'name="referrer" content="same-origin"')
        self.assertContains(page, 'referrerpolicy="same-origin"')
        self.assertNotContains(page, 'content="no-referrer"')
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        self.assertContains(page, label)
        token = client.cookies['csrftoken'].value
        body = {'filters': json.dumps(self.data), 'seller_id': '99999'}
        self.assertEqual(client.post(url, body, secure=True, HTTP_REFERER='https://testserver' + url).status_code, 403)
        other = Client(enforce_csrf_checks=True)
        other.get(url, secure=True)
        wrong = {**body, 'csrfmiddlewaretoken': other.cookies['csrftoken'].value}
        self.assertEqual(client.post(url, wrong, secure=True, HTTP_REFERER='https://testserver' + url).status_code, 403)
        with self.assertLogs('django.security.csrf', level='WARNING') as bare:
            bare_response = client.post(url, {**body, 'csrfmiddlewaretoken': token}, secure=True)
        self.assertEqual(bare_response.status_code, 403)
        self.assertIn('Referer checking failed - no Referer.', '\n'.join(bare.output))
        with self.assertLogs('django.security.csrf', level='WARNING') as foreign:
            foreign_response = client.post(
                url, {**body, 'csrfmiddlewaretoken': token}, secure=True,
                HTTP_ORIGIN='https://evil.example', HTTP_REFERER='https://testserver' + url,
            )
        self.assertEqual(foreign_response.status_code, 403)
        self.assertIn('Origin checking failed - https://evil.example', '\n'.join(foreign.output))
        with self.assertLogs('django.security.csrf', level='WARNING') as null_origin:
            null_response = client.post(
                url, {**body, 'csrfmiddlewaretoken': token}, secure=True,
                HTTP_ORIGIN='null', HTTP_REFERER='https://testserver' + url,
            )
        self.assertEqual(null_response.status_code, 403)
        self.assertIn('Origin checking failed - null', '\n'.join(null_origin.output))
        seller.refresh_from_db()
        self.assertIsNone(seller.request_filters)
        saved = client.post(
            url, {**body, 'csrfmiddlewaretoken': token}, secure=True,
            HTTP_REFERER='https://testserver' + url,
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved['Referrer-Policy'], 'same-origin')
        self.assertIn('saved=1', saved['Location'])
        seller.refresh_from_db()
        self.assertEqual(initial_filters(seller)['brands'][0]['model_ids'], [self.camry.pk])
        self.assertIn(self.optics.pk, initial_filters(seller)['category_ids'])
        reopened = client.get(url + '?saved=1', secure=True)
        self.assertContains(reopened, 'Настройки сохранены')
        self.assertEqual(reopened.context['filter_data'], initial_filters(seller))
        self.assertEqual(reopened['Referrer-Policy'], 'same-origin')
        cabinet_url = reverse('seller_request_filters')
        self._bind_parts_cabinet(client, seller)
        cabinet = client.get(cabinet_url, secure=True)
        self.assertEqual(cabinet.status_code, 200)
        self.assertEqual(cabinet['Referrer-Policy'], 'same-origin')
        self.assertEqual(cabinet.context['filter_data'], initial_filters(seller))
        updated = {**self.data, 'all_categories': True, 'category_ids': []}
        cabinet_saved = client.post(
            cabinet_url,
            {'filters': json.dumps(updated), 'csrfmiddlewaretoken': token},
            secure=True,
            HTTP_REFERER='https://testserver' + cabinet_url,
        )
        self.assertEqual(cabinet_saved.status_code, 302)
        seller.refresh_from_db()
        self.assertTrue(initial_filters(seller)['all_categories'])
        cabinet_again = client.get(cabinet_url + '?saved=1', secure=True)
        self.assertContains(cabinet_again, 'Настройки сохранены')
        self.assertTrue(cabinet_again.context['filter_data']['all_categories'])
        access.expires_at = timezone.now() - timedelta(seconds=1)
        access.save(update_fields=['expires_at'])
        blocked = {
            'filters': json.dumps({**self.data, 'all_brands': True, 'brands': []}),
            'csrfmiddlewaretoken': token,
        }
        self.assertEqual(
            client.post(url, blocked, secure=True, HTTP_REFERER='https://testserver' + url).status_code,
            410,
        )
        seller.refresh_from_db()
        self.assertTrue(initial_filters(seller)['all_categories'])
        self.assertFalse(initial_filters(seller)['all_brands'])
        self.assertEqual(client.post(
            '/sr/not-a-real-token/filters/',
            {**body, 'csrfmiddlewaretoken': token},
            secure=True,
            HTTP_REFERER='https://testserver/sr/not-a-real-token/filters/',
        ).status_code, 404)

    def test_https_same_origin_save_for_parts_and_dismantler(self):
        self._assert_https_parts_filter_save(self.seller, self.url, self.access, 'Новые запчасти')
        parts_filters = self.seller.request_filters
        dismantler = Seller.objects.create(
            name='Разбор', whatsapp='77011234568', transport_type='car', city='Алматы',
            receive_requests=True, sells_new_parts=False, is_dismantler=True,
            all_categories=True, all_brands=True, all_models=True, all_countries=True,
        )
        access = create_seller_request_access(request=self.req, seller=dismantler)
        self._assert_https_parts_filter_save(
            dismantler,
            reverse('seller_request_filters_link', kwargs={'token': access.token}),
            access,
            'Авторазбор',
        )
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.request_filters, parts_filters)

    def test_unbound_links_denied_and_test_owner_can_edit(self):
        access=create_seller_request_access(request=self.req)
        self.assertEqual(self.client.get(reverse('seller_request_filters_link',kwargs={'token':access.token})).status_code,404)
        self.seller.is_test_seller=True
        self.seller.test_all_requests_since=timezone.now()
        self.seller.save()
        self.assertContains(self.client.get('/sr/' + self.access.token + '/'), 'Настроить фильтр')
        self.assertContains(self.client.get(self.url), 'не ограничивает тестовую рассылку')
        self.assertEqual(self.client.post(self.url,{'filters':json.dumps(self.data)}).status_code,302)
        self.seller.refresh_from_db()
        self.assertIsNotNone(self.seller.request_filters)
        self.assertIsNotNone(self.seller.test_all_requests_since)
        self.seller.is_active=False;self.seller.save()
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_legacy_profile_uses_explicit_specialization_without_brand_fallback(self):
        self.assertIsNone(self.seller.request_filters)
        self.assertTrue(_find_matching_sellers(self.req)[0].exists())
        self.seller.all_brands=False;self.seller.brand='Lexus';self.seller.save()
        # A different declared brand no longer becomes eligible through fallback.
        self.assertFalse(_find_matching_sellers(self.req)[0].exists())

    def test_contact_save_cannot_overwrite_confirmed_filters(self):
        self.save();snapshot=self.seller.request_filters
        session=self.client.session;session['seller_id']=self.seller.pk;session.save()
        response=self.client.post(reverse('update_seller_profile'),json.dumps({'name':'Новое имя','all_brands':True,'selected_brand_ids':[],'transport_type':'truck'}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.seller.refresh_from_db();self.assertEqual(self.seller.request_filters,snapshot)
        self.assertEqual(self.seller.selected_brands.count(),2)
        self.assertEqual(self.seller.transport_type,'car')
