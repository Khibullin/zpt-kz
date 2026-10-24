"""Bound database work to the visible page, and keep SMTP off the response path."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from core.models import Request, Seller, Country, Brand, RequestInteraction
from core.services import request_connections as service
from core.services.operations_email_notifications import notify_new_request_async


@override_settings(TWO_WAY_REQUESTS_ENABLED=True)
class SellerPageLatencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Japan')
        Brand.objects.bulk_create([Brand(country=country, name=f'Other Brand {n}') for n in range(1000)])
        cls.req = Request.objects.create(brand='Toyota', model='Camry', city='Алматы',
            transport_type='car', description='Свечи', public_summary='Свечи')
        Seller.objects.bulk_create([Seller(name=f'Toyota shop {n}', city='Алматы', whatsapp='77001112233',
            sells_new_parts=True, all_brands=True, all_models=True, all_categories=True, transport_type='car')
            for n in range(120)])

    def setUp(self):
        cache.clear()

    def test_next_ten_hydrates_only_ten_current_sellers(self):
        first = service.buyer_seller_page(self.req, 1)
        with patch.object(service, 'directory_suggestions', wraps=service.directory_suggestions) as build:
            with CaptureQueriesContext(connection) as queries:
                second = service.buyer_seller_page(self.req, 2)
                names = [item['seller'].name for item in second]
            self.assertEqual(len(names), 10)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(len(build.call_args.kwargs['seller_ids']), 10)
            self.assertLessEqual(len(queries), 12)
        self.assertFalse({x['seller'].pk for x in first} & {x['seller'].pk for x in second})
        self.assertEqual(second.paginator.count, 120)

    def test_cached_order_never_restores_inactive_or_wrong_brand_shop(self):
        first = service.buyer_seller_page(self.req, 1)
        hidden, changed = first[0]['seller'], first[1]['seller']
        Seller.objects.filter(pk=hidden.pk).update(is_active=False)
        Seller.objects.filter(pk=changed.pk).update(name='Subaru shop')
        current = service.buyer_seller_page(self.req, 1)
        self.assertNotIn(hidden.pk, [x['seller'].pk for x in current])
        self.assertNotIn(changed.pk, [x['seller'].pk for x in current])
        self.assertEqual(service.directory_suggestions(self.req, seller_ids=[hidden.pk]), [])

    def test_geography_change_does_not_reuse_another_order(self):
        service.buyer_seller_page(self.req, 1)
        self.req.search_scope, self.req.city = 'city', 'Астана'
        self.assertEqual(list(service.buyer_seller_page(self.req, 1)), [])

    def test_impressions_are_one_bulk_write_and_do_not_mean_contact(self):
        page = service.buyer_seller_page(self.req, 1)
        with CaptureQueriesContext(connection) as queries:
            service.record_impressions(self.req, page)
        inserts = [q for q in queries if q['sql'].lstrip().upper().startswith('INSERT')]
        self.assertEqual(len(inserts), 1)
        service.record_impressions(self.req, page)
        self.assertEqual(RequestInteraction.objects.count(), 10)
        self.assertEqual(set(RequestInteraction.objects.values_list('kind', flat=True)), {'impression'})

    def test_lightweight_ranking_preserves_full_profile_rules_and_order(self):
        from core.models import CarModel, PartCategory
        country = Country.objects.first()
        toyota = Brand.objects.create(country=country, name='Toyota')
        camry = CarModel.objects.create(brand=toyota, name='Camry')
        other = CarModel.objects.create(brand=toyota, name='Other')
        category = PartCategory.objects.create(name='Свечи')
        self.req.category = category.name
        for index in range(12):
            seller = Seller.objects.create(name='Subaru shop' if index % 3 else 'Toyota shop',
                city='Алматы' if index % 2 else 'Астана', whatsapp='77001112233',
                sells_new_parts=True, transport_type='car', all_brands=index % 2 == 0,
                all_models=index % 3 == 0, all_categories=index % 4 == 0,
                brand_fk=toyota if index % 2 else None, model_fk=camry if index % 3 else other,
                directory_description='Description' if index % 2 else '')
            if index % 2:
                seller.selected_brands.add(toyota)
            if index % 3:
                seller.selected_models.add(camry)
            if index % 4:
                seller.selected_categories.add(category)
        expected = [x['seller'].pk for x in service.directory_suggestions(self.req)]
        self.assertEqual(service._directory_order(self.req, []), expected)
        excluded = expected[:3]
        self.assertEqual(service._directory_order(self.req, excluded), expected[3:])

    def test_specialization_uses_stored_brand_fields_without_guessing_from_name(self):
        from core.models import PartCategory
        seller = Seller.objects.first()
        # The imported name and broad routing flags alone are not a specialization.
        self.assertEqual(service.seller_specialization(seller), 'Специализацию уточните у продавца')
        country = Country.objects.first()
        brand = Brand.objects.create(country=country, name='Toyota')
        seller.brand_fk = brand
        seller.save(update_fields=['brand_fk'])
        seller.selected_brands.add(brand)
        page = service.buyer_seller_page(self.req, 1)
        item = next(item for item in page if item['seller'].pk == seller.pk)
        self.assertEqual(item['specialization'], 'Запчасти для Toyota')
        seller.brand_fk = None
        seller.save(update_fields=['brand_fk'])
        seller.selected_brands.clear()
        category = PartCategory.objects.create(name='Свечи зажигания')
        seller.selected_categories.add(category)
        self.assertEqual(service.seller_specialization(seller), category.name)

    def test_brand_word_index_matches_previous_overlapping_semantics(self):
        terms = {'land rover': 'land rover', 'rover': 'rover', 'toyota': 'toyota', 'тойота': 'toyota'}
        index = service._brand_term_index(terms)
        for name in ['Land Rover Toyota shop', 'Тойота Алматы', 'ToyotaParts', 'Rover-Land']:
            words = ' ' + service._brand_words(name) + ' '
            expected = {brand for term, brand in terms.items() if ' ' + term + ' ' in words}
            self.assertEqual(service._named_brands(name, index), expected)


@override_settings(REQUEST_OWNER_EMAIL_ENABLED=True, REQUEST_OWNER_EMAIL_ASYNC=True)
class OwnerEmailLatencyTests(SimpleTestCase):
    def test_slow_email_worker_does_not_hold_caller(self):
        started, release = Event(), Event()
        def slow_job(request_id, is_service):
            started.set()
            release.wait(5)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with patch('core.services.operations_email_notifications._get_owner_email_executor', return_value=executor), \
                 patch('core.services.operations_email_notifications._owner_email_job', side_effect=slow_job):
                try:
                    self.assertTrue(notify_new_request_async(123))
                    self.assertTrue(started.wait(2))
                    self.assertFalse(release.is_set())
                finally:
                    release.set()

    @override_settings(REQUEST_OWNER_EMAIL_ENABLED=False)
    def test_disabled_email_does_not_create_workers(self):
        with patch('core.services.operations_email_notifications._get_owner_email_executor') as executor:
            self.assertFalse(notify_new_request_async(123))
            executor.assert_not_called()
