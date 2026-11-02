import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlencode
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .importing import import_card_searches
from .models import Supplier, Offer, Membership, KaspiCardSearch


class CardSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('card-search-manager')
        Membership.objects.create(user=self.user, role='admin')
        self.supplier = Supplier.objects.create(name='Search supplier')
        self.offer = Offer.objects.create(supplier=self.supplier, brand='Brand', article='ABC123', name='Filter Brand ABC123', cost=100, stock='5')
        self.today = timezone.localdate()
        checks = []
        for kind, query in [('article', self.offer.article), ('brand_article', self.offer.brand+' '+self.offer.article), ('name', self.offer.name)]:
            checks.append({'kind': kind, 'query': query, 'source_url': 'https://kaspi.kz/shop/search/?'+urlencode({'text':query}),
                'city':'750000000', 'outcome':'no_exact_match', 'reviewed':True, 'all_pages_reviewed':True, 'result_titles':[]})
        self.row = {'brand':self.offer.brand, 'article':self.offer.article, 'name':self.offer.name, 'city':'750000000', 'complete':True, 'checks':checks}
        self.client.force_login(self.user)

    def upload(self, rows=None):
        payload = {'checked_on': self.today.isoformat(), 'offers': rows or [self.row]}
        file = SimpleUploadedFile('search.json', json.dumps(payload).encode())
        return import_card_searches(file, self.supplier.name, self.today, self.user)

    def test_missing_url_alone_does_not_qualify_but_reviewed_search_does(self):
        url = reverse('procurement:dashboard')
        self.assertEqual(self.client.get(url, {'mode':'no_sellers'}).context['total'], 0)
        self.assertEqual(self.upload(), 1)
        for presence in ('', 'no_card'):
            response = self.client.get(url, {'mode':'no_sellers', 'kaspi_presence':presence})
            self.assertEqual(response.context['total'], 1)
            self.assertContains(response, 'Карточка не найдена после проверки')
            self.assertContains(response, 'Цена продажи не задана')
        self.assertEqual(self.client.get(url, {'mode':'no_sellers','kaspi_presence':'has_card'}).context['total'], 0)

    def test_incomplete_error_wrong_source_or_exact_article_is_rejected(self):
        for changes in ({'all_pages_reviewed':False}, {'error':'timeout'}, {'source_url':'https://example.org/'}, {'result_titles':['Brand ABC123']}):
            row = deepcopy(self.row)
            row['checks'][0].update(changes)
            with self.assertRaises(ValueError):
                self.upload([row])
        self.assertFalse(KaspiCardSearch.objects.exists())

    def test_existing_link_is_not_reclassified_as_missing(self):
        self.offer.kaspi_url = 'https://kaspi.kz/shop/p/test-123/'
        self.offer.save()
        with self.assertRaises(ValueError):
            self.upload()

    def test_stale_search_changed_identity_or_no_stock_is_excluded(self):
        self.upload()
        url = reverse('procurement:dashboard')
        original = {'checked_on':self.today, 'checked_name':self.offer.name}
        for changes in ({'checked_on':self.today-timedelta(days=8)}, {'checked_on':self.today+timedelta(days=1)}, {'checked_name':'Old product'}):
            KaspiCardSearch.objects.filter(offer=self.offer).update(**{**original, **changes})
            self.assertEqual(self.client.get(url, {'mode':'no_sellers'}).context['total'], 0)
        KaspiCardSearch.objects.filter(offer=self.offer).update(**original)
        Offer.objects.filter(pk=self.offer.pk).update(stock='0')
        self.assertEqual(self.client.get(url, {'mode':'no_sellers'}).context['total'], 0)

    def test_import_is_atomic_and_does_not_set_match_or_demand(self):
        bad = deepcopy(self.row)
        bad['checks'][0]['reviewed'] = False
        with self.assertRaises(ValueError):
            self.upload([self.row, bad])
        self.assertFalse(KaspiCardSearch.objects.exists())
        self.upload()
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.match_verified)
        self.assertIsNone(self.offer.sales_30)
        self.assertIsNone(self.offer.review_count)
        response = self.client.get(reverse('procurement:detail', args=[self.offer.pk]))
        self.assertContains(response, 'Карточка Kaspi не найдена после проверки')
