from django.test import TestCase
from django.http import QueryDict
from django.core.exceptions import ValidationError
from core.models import Seller, Request, Country
from core.services.seller_matching import assess
from core.services.manual_seller_dispatch import validate_manual_recipient
from core.services.request_connections import relevant
from control_panel.manual_processing import recipient_rows


class SellerMatchingTests(TestCase):
    def setUp(self):
        self.req = Request.objects.create(source='home_short', transport_type='car',
            brand='Changan', model='CS35 / UNI-K', country='Китай', category='Электрика',
            city='Алматы', search_scope='city', phone='77010000000', description='Свечи')

    def seller(self, **kwargs):
        data = dict(name='Магазин', whatsapp='77010000001', transport_type='car',
            city='Алматы', sells_new_parts=True, brand='Changan', all_models=True,
            category='Электрика')
        data.update(kwargs)
        return Seller.objects.create(**data)

    def test_import_defaults_do_not_claim_specialization(self):
        unknown = self.seller(catalog_only=True, brand='', category='', all_brands=True, all_categories=True)
        self.assertEqual(assess(unknown, self.req).status, 'review')
        self.assertFalse(relevant(unknown, self.req))
        with self.assertRaises(ValidationError):
            validate_manual_recipient(self.req, unknown)
        rows = recipient_rows(self.req, QueryDict())
        self.assertEqual(rows[0]['match_status'], 'review')
        self.assertTrue(rows[0]['reason'])

    def test_wrong_brand_category_model_rejected(self):
        for changes in ({'brand':'Toyota'}, {'category':'Кузов'}, {'model':'CS75', 'all_models':False}):
            self.assertEqual(assess(self.seller(**changes), self.req).status, 'excluded')

    def test_each_requested_model_matches_and_reports_coverage(self):
        for model in ('CS35', 'UNI-K'):
            seller = self.seller(model=model, all_models=False)
            match = assess(seller, self.req)
            self.assertEqual(match.status, 'matched')
            self.assertEqual(match.models, (model,))
            validate_manual_recipient(self.req, seller)

    def test_json_multiple_models_and_restrictions(self):
        seller = self.seller(request_filters=dict(transport_types=['car'], all_categories=False,
            categories=[{'name':'Электрика'}], all_brands=False, brands=[dict(name='Changan',
            transport_type='car', all_models=False, models=[{'name':'UNI-K'}])]))
        self.assertEqual(assess(seller, self.req).status, 'matched')
        self.req.category = 'Кузов'
        self.assertEqual(assess(seller, self.req).status, 'excluded')

    def test_country_coverage_and_conflicting_country(self):
        china = Country.objects.create(name='Китай')
        japan = Country.objects.create(name='Япония')
        seller = self.seller(catalog_only=True, brand='', country_fk=china)
        self.assertEqual(assess(seller, self.req).status, 'matched')
        seller.country_fk = japan
        self.assertEqual(assess(seller, self.req).status, 'excluded')

    def test_geography_is_strict_even_for_specialist(self):
        seller = self.seller(city='Астана')
        self.assertFalse(relevant(seller, self.req))
        with self.assertRaises(ValidationError):
            validate_manual_recipient(self.req, seller)
        self.assertEqual(recipient_rows(self.req, QueryDict()), [])
        self.req.search_scope='custom'; self.req.selected_cities='Алматы, Шымкент'
        self.assertFalse(relevant(seller, self.req))

    def test_unknown_models_are_not_all_models(self):
        self.assertEqual(assess(self.seller(all_models=False), self.req).status, 'review')

    def test_registered_specialist_precedes_directory_and_unknown(self):
        unknown = self.seller(name='AAA', catalog_only=True, brand='', category='', all_brands=True, all_categories=True)
        directory = self.seller(name='BBB', catalog_only=True)
        registered = self.seller(name='ZZZ', password_hash='existing-password-hash')
        rows = recipient_rows(self.req, QueryDict())
        self.assertEqual([r['seller'].pk for r in rows], [registered.pk, directory.pk, unknown.pk])
        self.assertIn('Changan', rows[0]['match_reason'])
        from core.models import RequestDispatch
        self.assertFalse(RequestDispatch.objects.exists())

    def test_old_import_without_catalog_flag_is_not_registered(self):
        seller = self.seller(brand='', category='', all_brands=True, all_categories=True)
        self.assertEqual(assess(seller, self.req).status, 'review')

    def test_explicit_country_not_overridden_by_import_all_flag(self):
        seller = self.seller(brand='', all_brands=True, all_countries=True)
        seller.selected_countries.add(Country.objects.create(name='Корея'))
        self.assertEqual(assess(seller, self.req).status, 'excluded')

    def test_spark_plugs_match_both_existing_parent_categories(self):
        self.req.category = 'Двигатель'
        self.assertEqual(assess(self.seller(category='Электрика'), self.req).status, 'matched')
        self.assertEqual(assess(self.seller(category='Кузов'), self.req).status, 'excluded')

    def test_automatic_short_request_keeps_both_models_and_rejects_unknown(self):
        from django.test import override_settings
        from core.models import BroadcastSettings
        from core.views import _find_matching_sellers, _build_dispatch_queue
        settings = BroadcastSettings.load()
        settings.mode = 'live'; settings.emergency_stop = False; settings.save()
        first = self.seller(model='CS35', all_models=False, receive_requests=True)
        second = self.seller(model='UNI-K', all_models=False, receive_requests=True)
        self.seller(model='CS75', all_models=False, receive_requests=True)
        unknown = self.seller(brand='', category='', all_brands=True, all_categories=True, receive_requests=True)
        with override_settings(MANUAL_REQUEST_PROCESSING=False):
            sellers, _ = _find_matching_sellers(self.req)
            self.assertEqual(set(sellers.values_list('pk', flat=True)), {first.pk, second.pk})
            self.assertEqual(_build_dispatch_queue(self.req, [unknown]), [])

    def test_confirmed_multi_transport_filter_is_used_by_manual_validation(self):
        seller = self.seller(request_filters=dict(transport_types=['car', 'truck'],
            all_brands=True, all_categories=True))
        self.req.transport_type = 'truck'
        self.assertEqual(assess(seller, self.req).status, 'matched')
        validate_manual_recipient(self.req, seller)
