from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import QueryDict
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.admin import (
    BuyerContactAdmin,
    mark_buyer_contacts_as_test,
    unmark_buyer_contacts_as_test,
)
from core.buyer_contact_admin_filters import (
    PRIMARY_CITY_EMPTY,
    BuyerActivityFilter,
    BuyerBrandFilter,
    BuyerCategoryFilter,
    BuyerMarketingConsentFilter,
    BuyerModelFilter,
    BuyerPrimaryCityFilter,
    BuyerRequestCountFilter,
    BuyerTransportTypeFilter,
    build_category_summary,
    build_vehicle_summary,
    marketing_consent_label,
)
from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    BUYER_CONTACT_STATUS_UNSUBSCRIBED,
    BUYER_CITY_INTEREST_REQUEST_CITY,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_INFORMATION,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_PURPOSE_SERVICE,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerCategoryInterest,
    BuyerCityInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
)
from core.services.buyer_audience_service import eligible_buyer_contacts


def apply_filter(filter_class, value, queryset=None):
    factory = RequestFactory()
    request = factory.get('/admin/core/buyercontact/')
    params = QueryDict(mutable=True)
    params[filter_class.parameter_name] = value
    filter_instance = filter_class(
        request,
        params,
        BuyerContact,
        BuyerContactAdmin,
    )
    return filter_instance.queryset(request, queryset or BuyerContact.objects.all())


class BuyerContactAdminFilterTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.buyer_almaty = BuyerContact.objects.create(
            phone_normalized='77011111111',
            primary_city='Алматы',
            primary_country='Казахстан',
            requests_count=1,
            last_request_at=self.now - timedelta(days=3),
            last_search_scope='city',
        )
        self.buyer_astana = BuyerContact.objects.create(
            phone_normalized='77012222222',
            primary_city='Астана',
            requests_count=3,
            last_request_at=self.now - timedelta(days=20),
        )
        self.buyer_no_city = BuyerContact.objects.create(
            phone_normalized='77013333333',
            requests_count=2,
            last_request_at=self.now - timedelta(days=200),
        )
        self.buyer_inactive = BuyerContact.objects.create(
            phone_normalized='77014444444',
            requests_count=50,
            last_request_at=None,
        )
        self.buyer_test = BuyerContact.objects.create(
            phone_normalized='77015555555',
            is_test_contact=True,
            requests_count=1,
            last_request_at=self.now,
        )

        BuyerVehicle.objects.create(
            buyer=self.buyer_almaty,
            transport_type='car',
            brand='Toyota',
            model='Camry',
            last_seen_at=self.now,
        )
        BuyerVehicle.objects.create(
            buyer=self.buyer_astana,
            transport_type='truck',
            brand='Isuzu',
            model='Hilux',
            last_seen_at=self.now,
        )
        BuyerVehicle.objects.create(
            buyer=self.buyer_astana,
            transport_type='car',
            brand='Lexus',
            model='RX',
            last_seen_at=self.now,
        )

        BuyerCategoryInterest.objects.create(
            buyer=self.buyer_almaty,
            category='Ходовая',
            last_seen_at=self.now,
        )
        BuyerCategoryInterest.objects.create(
            buyer=self.buyer_astana,
            category='ходовая',
            last_seen_at=self.now,
        )
        BuyerCategoryInterest.objects.create(
            buyer=self.buyer_astana,
            category='Двигатель',
            last_seen_at=self.now,
        )

        ContactConsent.objects.create(
            buyer=self.buyer_almaty,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=self.now,
        )
        ContactConsent.objects.create(
            buyer=self.buyer_astana,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            revoked_at=self.now,
        )
        ContactConsent.objects.create(
            buyer=self.buyer_no_city,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_UNKNOWN,
        )

    def test_primary_city_filter(self):
        lookups = BuyerPrimaryCityFilter(
            RequestFactory().get('/'),
            QueryDict(''),
            BuyerContact,
            BuyerContactAdmin,
        ).lookups(None, None)
        normalized = next(value for value, label in lookups if label == 'Алматы')
        qs = apply_filter(BuyerPrimaryCityFilter, normalized)
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_almaty.pk})

    def test_primary_city_empty_filter(self):
        qs = apply_filter(BuyerPrimaryCityFilter, PRIMARY_CITY_EMPTY)
        self.assertIn(self.buyer_no_city.pk, qs.values_list('pk', flat=True))
        self.assertNotIn(self.buyer_almaty.pk, qs.values_list('pk', flat=True))

    def test_transport_type_filter(self):
        qs = apply_filter(BuyerTransportTypeFilter, 'truck')
        ids = set(qs.values_list('pk', flat=True))
        self.assertEqual(ids, {self.buyer_astana.pk})

    def test_brand_filter(self):
        qs = apply_filter(BuyerBrandFilter, 'toyota')
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_almaty.pk})

    def test_brand_filter_case_insensitive_lookups(self):
        filter_instance = BuyerBrandFilter(
            RequestFactory().get('/'),
            {},
            BuyerContact,
            BuyerContactAdmin,
        )
        lookup_values = [value for value, _ in filter_instance.lookups(None, None)]
        self.assertEqual(lookup_values.count('toyota'), 1)

    def test_model_filter(self):
        qs = apply_filter(BuyerModelFilter, 'camry')
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_almaty.pk})

    def test_category_filter(self):
        qs = apply_filter(BuyerCategoryFilter, 'ходовая')
        ids = set(qs.values_list('pk', flat=True))
        self.assertEqual(ids, {self.buyer_almaty.pk, self.buyer_astana.pk})

    def test_category_filter_case_insensitive_lookups(self):
        filter_instance = BuyerCategoryFilter(
            RequestFactory().get('/'),
            {},
            BuyerContact,
            BuyerContactAdmin,
        )
        lookup_values = [value for value, _ in filter_instance.lookups(None, None)]
        self.assertEqual(lookup_values.count('ходовая'), 1)

    def test_activity_last_7_days(self):
        qs = apply_filter(BuyerActivityFilter, 'last_7')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_almaty.pk, ids)
        self.assertIn(self.buyer_test.pk, ids)
        self.assertNotIn(self.buyer_astana.pk, ids)

    def test_activity_last_30_days(self):
        qs = apply_filter(BuyerActivityFilter, 'last_30')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_almaty.pk, ids)
        self.assertIn(self.buyer_astana.pk, ids)
        self.assertNotIn(self.buyer_no_city.pk, ids)

    def test_activity_over_180_days(self):
        qs = apply_filter(BuyerActivityFilter, 'over_180')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_no_city.pk, ids)
        self.assertNotIn(self.buyer_almaty.pk, ids)

    def test_activity_without_date(self):
        qs = apply_filter(BuyerActivityFilter, 'no_date')
        self.assertEqual(
            set(qs.values_list('pk', flat=True)),
            {self.buyer_inactive.pk},
        )

    def test_request_count_one(self):
        qs = apply_filter(BuyerRequestCountFilter, '1')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_almaty.pk, ids)
        self.assertIn(self.buyer_test.pk, ids)
        self.assertNotIn(self.buyer_astana.pk, ids)

    def test_request_count_two_to_four(self):
        qs = apply_filter(BuyerRequestCountFilter, '2_4')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_astana.pk, ids)
        self.assertIn(self.buyer_no_city.pk, ids)

    def test_request_count_fifty_plus(self):
        qs = apply_filter(BuyerRequestCountFilter, '50_plus')
        self.assertEqual(
            set(qs.values_list('pk', flat=True)),
            {self.buyer_inactive.pk},
        )

    def test_marketing_consent_granted(self):
        qs = apply_filter(BuyerMarketingConsentFilter, CONTACT_CONSENT_STATUS_GRANTED)
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_almaty.pk})

    def test_marketing_consent_unknown(self):
        qs = apply_filter(BuyerMarketingConsentFilter, 'unknown')
        ids = set(qs.values_list('pk', flat=True))
        self.assertIn(self.buyer_no_city.pk, ids)
        self.assertIn(self.buyer_inactive.pk, ids)
        self.assertIn(self.buyer_test.pk, ids)
        self.assertNotIn(self.buyer_almaty.pk, ids)
        self.assertNotIn(self.buyer_astana.pk, ids)

    def test_marketing_consent_revoked(self):
        qs = apply_filter(BuyerMarketingConsentFilter, CONTACT_CONSENT_STATUS_REVOKED)
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_astana.pk})

    def test_missing_marketing_consent_counts_as_unknown(self):
        buyer = BuyerContact.objects.create(
            phone_normalized='77016666666',
            requests_count=1,
        )
        qs = apply_filter(BuyerMarketingConsentFilter, 'unknown')
        self.assertIn(buyer.pk, qs.values_list('pk', flat=True))

    def test_is_test_contact_filter(self):
        qs = BuyerContact.objects.filter(is_test_contact=True)
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.buyer_test.pk})

    def test_related_filters_do_not_duplicate_buyers(self):
        qs = apply_filter(BuyerCategoryFilter, 'ходовая')
        self.assertEqual(qs.count(), qs.distinct().count())

    def test_vehicles_summary_limits_to_three(self):
        buyer = BuyerContact.objects.create(phone_normalized='77017777777')
        for index, model in enumerate(
            ['A', 'B', 'C', 'D', 'E'],
            start=1,
        ):
            BuyerVehicle.objects.create(
                buyer=buyer,
                transport_type='car',
                brand='Brand',
                model=model,
                last_seen_at=self.now - timedelta(minutes=index),
            )
        vehicles = list(buyer.vehicles.order_by('-last_seen_at', '-id'))
        summary = build_vehicle_summary(vehicles)
        self.assertIn('Brand A, Brand B, Brand C +2', summary)

    def test_categories_summary_limits_to_three(self):
        buyer = BuyerContact.objects.create(phone_normalized='77018888888')
        for name in ['A', 'B', 'C', 'D']:
            BuyerCategoryInterest.objects.create(
                buyer=buyer,
                category=name,
                last_seen_at=self.now,
            )
        interests = list(buyer.category_interests.order_by('-last_seen_at', '-id'))
        summary = build_category_summary(interests)
        self.assertIn('+1', summary)
        self.assertEqual(summary.count(','), 2)

    def test_marketing_consent_status_labels(self):
        self.assertEqual(
            marketing_consent_label(CONTACT_CONSENT_STATUS_GRANTED),
            'Разрешено',
        )
        self.assertEqual(
            marketing_consent_label(CONTACT_CONSENT_STATUS_UNKNOWN),
            'Не подтверждено',
        )
        self.assertEqual(
            marketing_consent_label(CONTACT_CONSENT_STATUS_REVOKED),
            'Отозвано',
        )
        self.assertEqual(marketing_consent_label(None), 'Не подтверждено')

    def test_admin_marketing_consent_status_display(self):
        admin = BuyerContactAdmin(BuyerContact, AdminSite())
        qs = admin.get_queryset(RequestFactory().get('/'))
        buyer = qs.get(pk=self.buyer_almaty.pk)
        self.assertEqual(admin.marketing_consent_status(buyer), 'Разрешено')

    def test_mark_buyer_contacts_as_test_action(self):
        factory = RequestFactory()
        request = factory.get('/')
        request.session = 'session'
        request._messages = FallbackStorage(request)
        queryset = BuyerContact.objects.filter(pk=self.buyer_almaty.pk)
        mark_buyer_contacts_as_test(None, request, queryset)
        self.buyer_almaty.refresh_from_db()
        self.assertTrue(self.buyer_almaty.is_test_contact)

    def test_unmark_buyer_contacts_as_test_action(self):
        factory = RequestFactory()
        request = factory.get('/')
        request.session = 'session'
        request._messages = FallbackStorage(request)
        queryset = BuyerContact.objects.filter(pk=self.buyer_test.pk)
        unmark_buyer_contacts_as_test(None, request, queryset)
        self.buyer_test.refresh_from_db()
        self.assertFalse(self.buyer_test.is_test_contact)


class EligibleBuyerContactsTests(TestCase):
    def setUp(self):
        self.active = BuyerContact.objects.create(
            phone_normalized='77019999999',
            status=BUYER_CONTACT_STATUS_ACTIVE,
        )
        self.test = BuyerContact.objects.create(
            phone_normalized='77018888888',
            is_test_contact=True,
        )
        self.blocked = BuyerContact.objects.create(
            phone_normalized='77017777777',
            status=BUYER_CONTACT_STATUS_BLOCKED,
        )
        self.unsubscribed = BuyerContact.objects.create(
            phone_normalized='77016666666',
            status=BUYER_CONTACT_STATUS_UNSUBSCRIBED,
        )

    def test_excludes_test_contacts(self):
        ids = set(eligible_buyer_contacts().values_list('pk', flat=True))
        self.assertNotIn(self.test.pk, ids)

    def test_excludes_blocked_and_unsubscribed(self):
        ids = set(eligible_buyer_contacts().values_list('pk', flat=True))
        self.assertNotIn(self.blocked.pk, ids)
        self.assertNotIn(self.unsubscribed.pk, ids)

    def test_keeps_active_real_contact(self):
        ids = set(eligible_buyer_contacts().values_list('pk', flat=True))
        self.assertIn(self.active.pk, ids)
