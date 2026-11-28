import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace as NS
from urllib.parse import urlsplit, parse_qs

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from repricer.identity import identity, article_in_title, execution_conflict
from repricer.apify_output import parse_apify_offers, import_apify_result
from repricer.apify_json import input_and_mapping, remaining_input_and_mapping
from repricer.market_policy import observation_time, is_fresh
from repricer.price_upload import price_plan
from repricer.models import KaspiActiveCompetitorOffer, KaspiActiveCityCheck
from .test_apify_output import ApifyOutputTests


class IdentityV2Tests(SimpleTestCase):
    def row(self, brand='AG Parts', catalog_brand='CTR'):
        return NS(listing=NS(product=NS(title='Рычаг CQ0735L',article='CQ0735L',brand=NS(name=catalog_brand)),
                            public_url='https://kaspi.kz/shop/p/test-123456789/',public_url_verified_at=timezone.now()),
                  product_name='Рычаг левый CQ0735L',merchant_sku='shop123',master_sku='123456789',brand=brand,
                  pk=1,listing_id=1,row_number=2,status='MATCHED',price=5000,quantity=1)

    def test_one_manufacturer_for_input_and_output(self):
        from repricer.apify_output import _search_match
        row=self.row()
        self.assertEqual(identity(row)[1],'CTR')
        self.assertTrue(_search_match(row,dict(title='CTR рычаг левый CQ0735L',brand='CTR')))
        self.assertFalse(_search_match(row,dict(title='OTHER рычаг CQ0735L',brand='OTHER')))

    def test_article_only_source_name_uses_existing_catalog_name(self):
        row=self.row();row.product_name='CQ0735L'
        self.assertEqual(identity(row)[0],'Рычаг CQ0735L')
        self.assertTrue(input_and_mapping([row],['Almaty'],20)[1][0]['searches'])

    def test_full_article_formatting_suffix_and_side(self):
        self.assertTrue(article_in_title('ML-9108R','MASUMA стойка ML 9108R'))
        self.assertFalse(article_in_title('ML-9108R','MASUMA ML9108L'))
        self.assertFalse(article_in_title('CQ0735L','CQ0735LX'))
        self.assertTrue(execution_conflict('Рычаг левый CQ0735L','Рычаг правый CQ0735L'))
        self.assertTrue(execution_conflict('Направляющая левая','Направляющая правая'))
        self.assertTrue(execution_conflict('2 шт.','1 шт.'))
        self.assertFalse(execution_conflict('левый / правый','правый'))

    def test_only_category_search_and_verified_own_card(self):
        data,mapping=input_and_mapping([self.row()],['Almaty'],20)
        self.assertEqual(data['searchQueries'],[])
        self.assertEqual(len(data['startUrls']),2)
        link=next(u for u in data['startUrls'] if '/shop/search/' in u)
        params=parse_qs(urlsplit(link).query)
        self.assertEqual(params['q'],[':category:Replacement parts'])
        self.assertEqual(params['text'],['Рычаг левый CQ0735L'])
        self.assertNotIn('+',link)
        self.assertTrue(data['includeOffers']);self.assertEqual(data['maxOffersPerProduct'],0)

    def test_wrong_side_and_unavailable_seller_never_set_minimum(self):
        row=self.row()
        item=dict(id='123456789',url=row.listing.public_url,title='CTR рычаг правый CQ0735L',brand='CTR',city='Almaty',offers=[dict(sellerId='other',price=1)])
        self.assertEqual(parse_apify_offers([item],[row],['Almaty'])['evidence'],[])
        item['title']='CTR рычаг левый CQ0735L'
        item['offers'][0]['inStock']=False
        self.assertEqual(parse_apify_offers([item],[row],['Almaty'])['evidence'],[])
        item['offers'].append(dict(sellerId='available',price=3000))
        self.assertEqual(parse_apify_offers([item],[row],['Almaty'])['evidence'][0]['price'],3000)

    def test_unknown_brand_needs_verified_card(self):
        from repricer.apify_output import _search_match
        row=self.row(brand='',catalog_brand='')
        item=dict(title=row.product_name,brand='CTR')
        self.assertFalse(_search_match(row,item))
        self.assertTrue(_search_match(row,item,verified_card=True))

    def test_upload_time_cannot_refresh_an_old_observation(self):
        now=timezone.now();old=now-timedelta(days=3)
        self.assertEqual(observation_time([dict(scrapedAt=old.isoformat())],now),old)
        self.assertFalse(is_fresh(old))
        with self.assertRaises(ValueError):observation_time([dict(id='1')])


class MonitoringPolicyFlowTests(TestCase):
    setUp = ApifyOutputTests.setUp
    def card(self, city='Almaty', price=2000, **kwargs):
        return dict(id='141407587',url=self.listing.public_url,title=self.product.title,city=city,
                    offers=[dict(sellerId='other',price=price)],**kwargs)

    def ready(self):
        from procurement.models import Activity
        now=timezone.now()
        import_apify_result([self.card(),self.card('Astana',2100)],self.batch,filename='fresh',captured_at=now)
        Activity.objects.create(user=self.staff,action='repricer_price_floor',details={'batch_id':self.batch.pk,'row_id':self.active.pk,'price':1500})

    def test_fresh_verified_quotes_and_floor_allow_only_safe_reduction(self):
        self.ready();p=next(x for x in price_plan(self.batch,self.staff) if x['id']==self.active.pk)
        self.assertEqual(p['new'],1940);self.assertEqual(p['reason'],'')
        self.assertEqual(p['floor'],1500)

    def test_missing_city_stale_data_and_low_price_block_export(self):
        self.ready()
        KaspiActiveCityCheck.objects.filter(active_row=self.active,city='Astana').delete()
        p=next(x for x in price_plan(self.batch,self.staff) if x['id']==self.active.pk)
        self.assertIn('городов',p['reason'])
        old=timezone.now()-timedelta(days=2)
        KaspiActiveCompetitorOffer.objects.filter(active_row=self.active).update(checked_at=old)
        p=next(x for x in price_plan(self.batch,self.staff) if x['id']==self.active.pk)
        self.assertIn('свежей',p['reason'])

    def test_unknown_floor_blocked_and_manual_minimum_is_audited(self):
        self.ready()
        from procurement.models import Activity
        Activity.objects.filter(action='repricer_price_floor').delete()
        page=self.client.get(reverse('repricer:prepare_prices'))
        self.assertContains(page,'Укажите минимальную допустимую цену')
        response=self.client.post(reverse('repricer:prepare_prices'),dict(action='save_floors',
            plan_token=page.context['plan_token'],**{'floor_'+str(self.active.pk):'1500'}))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Activity.objects.get(action='repricer_price_floor').details['price'],1500)

    def test_direct_upload_requires_observation_date(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload=SimpleUploadedFile('old.json',json.dumps([self.card()]).encode())
        response=self.client.post(reverse('repricer:apify_output'),{'apify_output':upload},follow=True)
        self.assertContains(response,'Укажите дату и время запуска Apify')
        self.assertFalse(KaspiActiveCompetitorOffer.objects.exists())

    def test_below_floor_and_known_cost_never_export(self):
        self.ready()
        from procurement.models import Activity
        Activity.objects.create(user=self.staff, action='repricer_price_floor',
            details={'batch_id':self.batch.pk,'row_id':self.active.pk,'price':2100})
        plan = price_plan(self.batch,self.staff)
        self.assertIn('ниже допустимого',next(x for x in plan if x['id']==self.active.pk)['reason'])
        self.product.cost_price = Decimal('2500')
        self.product.save()
        Activity.objects.create(user=self.staff, action='repricer_price_floor',
            details={'batch_id':self.batch.pk,'row_id':self.active.pk,'price':100})
        plan = price_plan(self.batch,self.staff)
        self.assertGreaterEqual(next(x for x in plan if x['id']==self.active.pk)['floor'],2500)

    def test_new_city_requires_its_own_fresh_check(self):
        self.ready()
        from repricer.models import KaspiRepricerSearchSettings
        KaspiRepricerSearchSettings.objects.filter(pk=1).update(cities=['Almaty','Astana','Shymkent'])
        plan = price_plan(self.batch,self.staff)
        self.assertIn('городов',next(x for x in plan if x['id']==self.active.pk)['reason'])

    def test_dashboard_counts_current_input_and_selected_cities(self):
        self.ready()
        page=self.client.get(reverse('repricer:dashboard'))
        self.assertEqual(page.context['apify_saved_city_count'],2)
        self.assertEqual(page.context['apify_input_ready'],1)
        self.assertEqual(page.context['apify_input_missing'][0]['sku'],'UNMAPPED')
        self.assertContains(page,'Городов с сохранёнными проверками: 2 из 2')
        self.assertContains(page,'Не включены в INPUT:')

    def test_stale_rows_are_included_in_followup_and_tagged_in_dashboard(self):
        self.ready();old=timezone.now()-timedelta(days=2)
        self.active.competitor_checked_at=old;self.active.save()
        KaspiActiveCompetitorOffer.objects.filter(active_row=self.active).update(checked_at=old)
        data,mapping=remaining_input_and_mapping([self.active],['Almaty'],20)
        self.assertTrue(data['startUrls'])
        self.assertContains(self.client.get(reverse('repricer:dashboard')),'Устарело')
