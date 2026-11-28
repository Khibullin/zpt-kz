import json
from datetime import date
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from .kaspi_category_search import (SEARCH_POLICY, category_mapping, search_entry,
                                    explicit_non_parts_category)
from .models import Activity, Membership, PriceUpdate, Supplier, WholesaleSelectionRule
from .omega_selection_requests import REQUEST_ACTION, request_queries, preview_request, save_request


class CategoryQueryTests(SimpleTestCase):
    def test_complete_article_with_name_and_verified_category_only(self):
        row = dict(brand='MASUMA', article='ML-9108R',
                   name='MASUMA стойка стабилизатора ML 9108R', category='Стойка стабилизатора')
        entry = search_entry(row)
        params = parse_qs(urlsplit(entry['url']).query)
        self.assertEqual(params['text'], ['стойка стабилизатора ML-9108R'])
        self.assertEqual(params['q'], [':category:Anti Roll Bars'])
        self.assertEqual(params['sort'], ['relevance'])
        self.assertNotIn('+', entry['url'])
        self.assertIn('%20', entry['url'])
        self.assertEqual(len(category_mapping([row, row])), 1)
        self.assertEqual(entry['original_article'], 'ML-9108R')

    def test_other_categories_keep_parts_scope_and_never_bare_article(self):
        entry = search_entry(dict(brand='TEST', article='00100L', name='TEST 00100L', category='Рычаг'))
        self.assertEqual(entry['query'], 'Рычаг 00100L')
        self.assertEqual(entry['category'], 'Replacement parts')
        with self.assertRaises(ValueError):
            search_entry(dict(brand='TEST', article='A1', name='TEST A1', category='Без категории'))

    def test_explicit_non_parts_category_rejected_but_missing_stays_unknown(self):
        self.assertTrue(explicit_non_parts_category('Товары для дома > Бумажные салфетки'))
        self.assertFalse(explicit_non_parts_category('Автотовары > Автозапчасти > Ходовая часть'))
        self.assertFalse(explicit_non_parts_category(None))


class CategoryInputFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('category-input-admin')
        Membership.objects.create(user=self.user, role='admin')
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Phaeton DC')
        WholesaleSelectionRule.objects.create(singleton_key=1, supplier=self.supplier)
        self.price = PriceUpdate.objects.create(supplier=self.supplier, user=self.user,
            filename='latest.xlsx', checked_on=date.today(), sha256='immutable', status='done', row_count=2,
            rows=[dict(brand='MASUMA',article='ML-9108R',name='Стойка стабилизатора',
                       category='Стойка стабилизатора',cost='1500',stock='3'),
                  dict(brand='MASUMA',article='ML-9108L',name='Стойка стабилизатора',
                       category='Стойка стабилизатора',cost='2500',stock='2')])

    def test_new_preview_save_and_export_have_same_scope_and_count(self):
        criteria = dict(batch_id=self.price.pk,categories=['Стойка стабилизатора'],minimum='1000',max_vehicle_age=20)
        preview = preview_request(**criteria)
        saved = save_request(**criteria,user=self.user)
        self.assertEqual(preview['queries'],2)
        self.assertEqual(saved.details['query_count'],2)
        self.assertEqual(saved.details['search_policy'],SEARCH_POLICY)
        response = self.client.get(reverse('procurement:selection_apify_json'),
            {'request_id':saved.pk,'city':['Almaty','Astana']})
        self.assertEqual(response.status_code,200,response.content)
        data = json.loads(response.content)
        self.assertEqual(data['searchQueries'],[])
        self.assertEqual(len(data['startUrls']),2)
        self.assertTrue(all(parse_qs(urlsplit(url).query)['q']==[':category:Anti Roll Bars'] for url in data['startUrls']))
        self.assertEqual(data['cities'],['Almaty','Astana'])
        self.assertEqual(data['sortOrder'],'relevance')
        self.assertTrue(data['includeOffers'])
        self.assertEqual(data['maxOffersPerProduct'],0)
        self.assertFalse(data['includeReviews'])

    def test_old_request_upgrade_is_explicit_idempotent_and_preserves_snapshot(self):
        old = save_request(batch_id=self.price.pk,categories=['Стойка стабилизатора'],minimum='1000',user=self.user)
        old.details['search_policy']='name_article_v5';old.details['query_count']=4
        old.save(update_fields=['details']); before=dict(old.details)
        blocked = self.client.get(reverse('procurement:selection_apify_json'), {'request_id':old.pk})
        self.assertEqual(blocked.status_code,400)
        url = reverse('procurement:selection_rules')
        payload = dict(action='upgrade_apify_category_request',supplier=self.supplier.pk,request_id=old.pk)
        first = self.client.post(url,payload)
        self.assertEqual(first.status_code,200,first.content)
        second = self.client.post(url,payload)
        self.assertEqual(first.json()['request_id'],second.json()['request_id'])
        new = Activity.objects.get(pk=first.json()['request_id'])
        for key in ('price_batch_id','price_sha256','min_cost','category_keys','total'):
            self.assertEqual(new.details[key],before[key])
        self.assertEqual(new.details['query_count'],2)
        self.assertEqual(new.details['upgraded_from'],old.pk)
        old.refresh_from_db();self.assertEqual(old.details,before)
        self.assertEqual(self.client.session['apify_prepared_request_id'],new.pk)

    def test_1000_batch_links_cover_all_rows_and_city_does_not_change_count(self):
        saved = save_request(batch_id=self.price.pk,categories=['Стойка стабилизатора'],minimum='1000',user=self.user)
        mapping = [search_entry(dict(brand='TEST',article=str(i),name='Рычаг',category='Рычаг')) for i in range(2394)]
        with patch('procurement.omega_selection_requests.request_queries',return_value=mapping):
            for cities in (['Almaty'],['Almaty','Astana','Shymkent']):
                urls=[]
                for batch in range(1,4):
                    response=self.client.get(reverse('procurement:selection_apify_json'),
                        {'request_id':saved.pk,'batch_size':1000,'batch':batch,'city':cities})
                    self.assertEqual(response.status_code,200,response.content)
                    urls.extend(json.loads(response.content)['startUrls'])
                self.assertEqual(urls,[entry['url'] for entry in mapping])

    def test_other_supplier_request_cannot_be_upgraded(self):
        saved=save_request(batch_id=self.price.pk,categories=['Стойка стабилизатора'],minimum='1000',user=self.user)
        other=Supplier.objects.create(name='Omega')
        response=self.client.post(reverse('procurement:selection_rules'),
            dict(action='upgrade_apify_category_request',supplier=other.pk,request_id=saved.pk))
        self.assertEqual(response.status_code,400)
