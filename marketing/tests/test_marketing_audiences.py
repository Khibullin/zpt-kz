from __future__ import annotations

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerCategoryInterest,
    BuyerCityInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
    Seller,
)
from core.services.buyer_contact_utils import mask_phone, normalize_buyer_text
from marketing.models import MarketingAudience, MarketingCabinetPermission
from marketing.services.audiences import (
    GROUP_BUYERS,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_PARTS_REQUESTS,
    calculate_audience,
    normalize_marketing_criteria,
)
from marketing.services.audiences.constants import SUBTYPE_SERVICE_REQUESTS
from marketing.services.audiences.filters import criteria_raw_from_request_post
from marketing.services.audiences.validation import (
    CriteriaValidationError,
    validate_and_normalize_criteria,
)
from marketing.services.audiences.constants import (
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    GROUP_TEST,
    SUBTYPE_COMBINED_SELLERS,
    SUBTYPE_DETAILING,
    SUBTYPE_REQUEST_SELLERS,
    SUBTYPE_STO,
    SUBTYPE_TEST_CONTACTS,
)
from marketing.services.contacts import build_contact_registry
from orders.models import Order
from service_requests.models import Service, ServiceRequest, ServiceSeller

_phone_counter = 9300000


def next_phone() -> str:
    global _phone_counter
    _phone_counter += 1
    return f'77{_phone_counter:09d}'[-11:]


def grant_marketing_permission(user: User) -> None:
    content_type = ContentType.objects.get_for_model(MarketingCabinetPermission)
    permission = Permission.objects.get(
        content_type=content_type,
        codename='access_marketing_cabinet',
    )
    user.user_permissions.add(permission)


def make_buyer(**kwargs) -> BuyerContact:
    defaults = {
        'phone_normalized': next_phone(),
        'status': BUYER_CONTACT_STATUS_ACTIVE,
        'primary_city': 'Алматы',
        'requests_count': 1,
        'last_request_at': timezone.now(),
    }
    defaults.update(kwargs)
    return BuyerContact.objects.create(**defaults)


def grant_consent(buyer: BuyerContact, status=CONTACT_CONSENT_STATUS_GRANTED) -> None:
    payload = {
        'buyer': buyer,
        'channel': CONTACT_CONSENT_CHANNEL_WHATSAPP,
        'purpose': CONTACT_CONSENT_PURPOSE_MARKETING,
        'status': status,
        'consented_at': timezone.now(),
    }
    if status == CONTACT_CONSENT_STATUS_REVOKED:
        payload['revoked_at'] = timezone.now()
    ContactConsent.objects.create(**payload)


class MarketingAudienceAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)

    def test_permission_required(self):
        response = self.client.get(reverse('marketing:audiences'))
        self.assertEqual(response.status_code, 302)
        staff = User.objects.create_user('staff', password='secret', is_staff=True)
        grant_marketing_permission(staff)
        self.client.login(username='staff', password='secret')
        response = self.client.get(reverse('marketing:audiences'))
        self.assertEqual(response.status_code, 200)


class MarketingAudienceCalculatorTests(TestCase):
    def test_parts_requests_primary_city_filter(self):
        make_buyer(primary_city='Алматы')
        make_buyer(primary_city='Астана')
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'primary_cities': ['Алматы']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_parts_requests_single_search_city_filter(self):
        buyer = make_buyer(primary_city='Астана')
        BuyerCityInterest.objects.create(
            buyer=buyer,
            city='Алматы',
            city_normalized=normalize_buyer_text('Алматы'),
            interest_type='selected_city',
        )
        make_buyer(primary_city='Алматы')
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'search_cities': ['Алматы']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_parts_requests_multiple_search_cities_or(self):
        buyer_almaty = make_buyer(primary_city='Шымкент')
        BuyerCityInterest.objects.create(
            buyer=buyer_almaty,
            city='Алматы',
            city_normalized=normalize_buyer_text('Алматы'),
            interest_type='selected_city',
        )
        buyer_astana = make_buyer(primary_city='Шымкент')
        BuyerCityInterest.objects.create(
            buyer=buyer_astana,
            city='Астана',
            city_normalized=normalize_buyer_text('Астана'),
            interest_type='selected_city',
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'search_cities': ['Алматы', 'Астана']},
        )
        self.assertEqual(result.matched_count, 2)

    def test_parts_requests_kazakhstan_search_scope(self):
        buyer = make_buyer(primary_city='Алматы', last_search_scope='kazakhstan')
        make_buyer(primary_city='Астана', last_search_scope='city')
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'search_scopes': ['kazakhstan']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_primary_city_does_not_substitute_search_city(self):
        buyer = make_buyer(primary_city='Алматы')
        BuyerCityInterest.objects.create(
            buyer=buyer,
            city='Астана',
            city_normalized=normalize_buyer_text('Астана'),
            interest_type='selected_city',
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'search_cities': ['Астана']},
        )
        self.assertEqual(result.matched_count, 1)
        result_primary = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'primary_cities': ['Астана']},
        )
        self.assertEqual(result_primary.matched_count, 0)

    def test_parts_requests_single_city_filter(self):
        buyer = make_buyer(primary_city='Алматы')
        make_buyer(primary_city='Астана')
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'cities': ['Алматы']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_parts_requests_multiple_cities_or(self):
        make_buyer(primary_city='Алматы')
        make_buyer(primary_city='Астана')
        make_buyer(primary_city='Шымкент')
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'cities': ['Алматы', 'Астана']},
        )
        self.assertEqual(result.matched_count, 2)

    def test_parts_requests_brand_filter(self):
        buyer = make_buyer()
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Toyota',
            brand_normalized=normalize_buyer_text('Toyota'),
            model='Camry',
            model_normalized=normalize_buyer_text('Camry'),
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'brands': ['Toyota']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_parts_requests_model_filter(self):
        buyer = make_buyer()
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Toyota',
            brand_normalized=normalize_buyer_text('Toyota'),
            model='Camry',
            model_normalized=normalize_buyer_text('Camry'),
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'models': ['Camry']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_brand_and_model_same_vehicle(self):
        buyer = make_buyer()
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Toyota',
            brand_normalized=normalize_buyer_text('Toyota'),
            model='Camry',
            model_normalized=normalize_buyer_text('Camry'),
        )
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Lexus',
            brand_normalized=normalize_buyer_text('Lexus'),
            model='RX',
            model_normalized=normalize_buyer_text('RX'),
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'brands': ['Toyota'], 'models': ['Camry']},
        )
        self.assertEqual(result.matched_count, 1)

    def test_category_with_period(self):
        buyer = make_buyer()
        BuyerCategoryInterest.objects.create(
            buyer=buyer,
            category='фильтры',
            category_normalized=normalize_buyer_text('фильтры'),
            last_seen_at=timezone.now(),
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'categories': ['фильтры'], 'category_period': '90'},
        )
        self.assertEqual(result.matched_count, 1)

    def test_marketplace_paid_counts_paid_order(self):
        phone = next_phone()
        Order.objects.create(
            customer_name='Buyer',
            customer_phone=phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_PAID,
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_MARKETPLACE_PAID,
            criteria={},
        )
        self.assertGreaterEqual(result.matched_count, 1)

    def test_marketplace_new_orders_not_counted(self):
        phone = next_phone()
        Order.objects.create(
            customer_name='Buyer',
            customer_phone=phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_NEW,
        )
        registry = build_contact_registry()
        self.assertNotIn(phone, registry)

    def test_marketplace_test_buyers_separated(self):
        test_phone = '77011910000'
        BuyerContact.objects.create(
            phone_normalized=test_phone,
            status=BUYER_CONTACT_STATUS_ACTIVE,
            is_test_contact=True,
        )
        Order.objects.create(
            customer_name='Test buyer',
            customer_phone=test_phone,
            total_price=1000,
            delivery_method=Order.DELIVERY_PICKUP,
            status=Order.STATUS_PAID,
        )
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_MARKETPLACE_PAID,
            criteria={},
        )
        self.assertGreaterEqual(result.marketplace_test_count, 1)

    def test_request_sellers_filter(self):
        phone = next_phone()
        Seller.objects.create(
            name='Parts seller',
            whatsapp=phone,
            city='Алматы',
            is_active=True,
            receive_requests=True,
        )
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={'cities': ['Алматы']},
        )
        self.assertGreaterEqual(result.matched_count, 1)

    def test_marketplace_sellers_with_products(self):
        phone = next_phone()
        user = User.objects.create_user(f'seller_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Shop', phone=phone, city='Алматы')
        Product.objects.create(
            title='Part',
            slug='part-1',
            article='P-1',
            price=1000,
            whatsapp_number=phone,
            status='active',
        )
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype='marketplace_sellers',
            criteria={'has_products': True},
        )
        self.assertGreaterEqual(result.matched_count, 1)

    def test_combined_sellers_dedup_by_phone(self):
        phone = next_phone()
        Seller.objects.create(name='Combined', whatsapp=phone, city='Алматы', is_active=True)
        user = User.objects.create_user(f'combined_{phone}', password='secret')
        SellerProfile.objects.create(user=user, name='Combined', phone=phone, city='Алматы')
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_COMBINED_SELLERS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.unique_phones, 1)

    def test_sto_city_filter(self):
        phone = next_phone()
        ServiceSeller.objects.create(
            name='STO Shop',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            criteria={'cities': ['Алматы']},
        )
        self.assertGreaterEqual(result.matched_count, 1)

    def test_service_customer_filters_by_service(self):
        service = Service.objects.create(name='Замена масла')
        phone = next_phone()
        request = ServiceRequest.objects.create(
            service_type='sto',
            city='Алматы',
            phone=phone,
        )
        request.services.add(service)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_SERVICE_REQUESTS,
            criteria={'services': [service.id]},
        )
        self.assertEqual(result.matched_count, 1)

    def test_sto_filters_by_service(self):
        service = Service.objects.create(name='Диагностика')
        phone = next_phone()
        seller = ServiceSeller.objects.create(
            name='STO Shop',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        seller.services.add(service)
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            criteria={'services': [service.id]},
        )
        self.assertEqual(result.matched_count, 1)

    def test_detailing_filters_by_service(self):
        service = Service.objects.create(name='Полировка')
        phone = next_phone()
        seller = ServiceSeller.objects.create(
            name='Detail Pro',
            whatsapp=phone,
            city='Астана',
            seller_type='detailing',
            password='hash',
            is_active=True,
        )
        seller.services.add(service)
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            criteria={'services': [service.id]},
        )
        self.assertEqual(result.matched_count, 1)

    def test_multiple_services_or_logic(self):
        service_a = Service.objects.create(name='Мойка')
        service_b = Service.objects.create(name='Химчистка')
        phone = next_phone()
        seller = ServiceSeller.objects.create(
            name='Detail Pro',
            whatsapp=phone,
            city='Астана',
            seller_type='detailing',
            password='hash',
            is_active=True,
        )
        seller.services.add(service_a)
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            criteria={'services': [service_a.id, service_b.id]},
        )
        self.assertEqual(result.matched_count, 1)

    def test_sto_service_not_matched_as_detailing(self):
        service = Service.objects.create(name='Развал-схождение')
        phone = next_phone()
        seller = ServiceSeller.objects.create(
            name='STO Only',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        seller.services.add(service)
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            criteria={'services': [service.id]},
        )
        self.assertEqual(result.matched_count, 0)

    def test_detailing_service_filter(self):
        phone = next_phone()
        seller = ServiceSeller.objects.create(
            name='Detail Pro',
            whatsapp=phone,
            city='Астана',
            seller_type='detailing',
            password='hash',
            is_active=True,
        )
        service = Service.objects.create(name='Полировка')
        seller.services.add(service)
        ServiceRequest.objects.create(
            service_type='detailing',
            city='Астана',
            phone=next_phone(),
        )
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            criteria={'cities': ['Астана']},
        )
        self.assertGreaterEqual(result.matched_count, 1)

    def test_granted_buyer_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={'cities': ['Алматы']},
        )
        self.assertGreaterEqual(result.eligible_count, 1)

    def test_unknown_buyer_not_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer, CONTACT_CONSENT_STATUS_UNKNOWN)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.unknown_count, 1)

    def test_revoked_buyer_not_eligible(self):
        buyer = make_buyer()
        grant_consent(buyer, CONTACT_CONSENT_STATUS_REVOKED)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.revoked_count, 1)

    def test_seller_without_consent_not_eligible(self):
        phone = next_phone()
        Seller.objects.create(name='Seller', whatsapp=phone, city='Алматы', is_active=True)
        result = calculate_audience(
            contact_group=GROUP_SELLERS,
            contact_subtype=SUBTYPE_REQUEST_SELLERS,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)
        self.assertGreaterEqual(result.consent_not_recorded_count, 1)

    def test_sto_without_consent_not_eligible(self):
        phone = next_phone()
        ServiceSeller.objects.create(
            name='STO',
            whatsapp=phone,
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        )
        result = calculate_audience(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            criteria={},
        )
        self.assertEqual(result.eligible_count, 0)

    def test_test_audience_only_test_granted(self):
        test_buyer = make_buyer(is_test_contact=True)
        grant_consent(test_buyer, CONTACT_CONSENT_STATUS_GRANTED)
        real_buyer = make_buyer(is_test_contact=False)
        grant_consent(real_buyer, CONTACT_CONSENT_STATUS_GRANTED)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 1)

    def test_preview_masks_phone(self):
        buyer = make_buyer()
        grant_consent(buyer)
        result = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria={},
        )
        self.assertTrue(result.preview_rows)
        self.assertEqual(result.preview_rows[0].masked_phone, mask_phone(buyer.phone_normalized))
        self.assertNotIn(buyer.phone_normalized, result.preview_rows[0].masked_phone)

    def test_criteria_roundtrip(self):
        raw = {
            'primary_cities': ['Алматы', 'Астана'],
            'brands': ['Toyota'],
            'category_period': '90',
        }
        normalized = normalize_marketing_criteria(
            raw,
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
        self.assertEqual(normalized['primary_cities'], ['Алматы', 'Астана'])
        self.assertEqual(normalized['brands'], ['Toyota'])
        self.assertEqual(normalized['category_period'], '90')


class MarketingAudienceServiceOptionsTests(TestCase):
    def _make_seller(self, seller_type: str) -> ServiceSeller:
        return ServiceSeller.objects.create(
            name=f'{seller_type} seller',
            whatsapp=next_phone(),
            city='Алматы',
            seller_type=seller_type,
            password='hash',
            is_active=True,
        )

    def test_sto_only_service_in_sto_options_only(self):
        service = Service.objects.create(name='СТО only')
        seller = self._make_seller('sto')
        seller.services.add(service)

        from marketing.services.audiences.options import build_audience_filter_options

        registry = build_contact_registry()
        sto_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            registry=registry,
        )
        detailing_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            registry=registry,
        )

        sto_ids = {item['id'] for item in sto_options['services_sto']}
        detailing_ids = {item['id'] for item in detailing_options['services_detailing']}
        self.assertIn(service.id, sto_ids)
        self.assertNotIn(service.id, detailing_ids)

    def test_dual_seller_service_in_both_lists(self):
        service = Service.objects.create(name='Универсальная услуга')
        self._make_seller('sto').services.add(service)
        self._make_seller('detailing').services.add(service)

        from marketing.services.audiences.options import build_audience_filter_options

        registry = build_contact_registry()
        sto_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            registry=registry,
        )
        detailing_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            registry=registry,
        )

        self.assertIn(service.id, {item['id'] for item in sto_options['services_sto']})
        self.assertIn(service.id, {item['id'] for item in detailing_options['services_detailing']})

    def test_orphan_service_not_in_options_and_no_error(self):
        orphan = Service.objects.create(name='Без исполнителей')

        from marketing.services.audiences.options import build_audience_filter_options

        registry = build_contact_registry()
        sto_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
            registry=registry,
        )
        detailing_options = build_audience_filter_options(
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
            registry=registry,
        )

        all_ids = {
            item['id']
            for item in sto_options['services_sto'] + detailing_options['services_detailing']
        }
        self.assertNotIn(orphan.id, all_ids)
        self.assertEqual(orphan._meta.get_field('name').name, 'name')
        self.assertFalse(hasattr(orphan, 'seller_type'))

    def test_normalize_strips_wrong_service_ids_by_subtype(self):
        sto_service = Service.objects.create(name='Развал')
        detailing_service = Service.objects.create(name='Полировка')
        self._make_seller('sto').services.add(sto_service)
        self._make_seller('detailing').services.add(detailing_service)

        sto_normalized = normalize_marketing_criteria(
            {'services': [sto_service.id, detailing_service.id]},
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
        )
        detailing_normalized = normalize_marketing_criteria(
            {'services': [sto_service.id, detailing_service.id]},
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
        )

        self.assertEqual(sto_normalized['services'], [sto_service.id])
        self.assertEqual(detailing_normalized['services'], [detailing_service.id])

    def test_request_post_strips_wrong_service_ids_by_subtype(self):
        sto_service = Service.objects.create(name='Диагностика')
        detailing_service = Service.objects.create(name='Химчистка')
        self._make_seller('sto').services.add(sto_service)
        self._make_seller('detailing').services.add(detailing_service)

        from django.http import QueryDict

        post_data = QueryDict(mutable=True)
        post_data.setlist('services', [str(sto_service.id), str(detailing_service.id)])

        sto_criteria = validate_and_normalize_criteria(
            criteria_raw_from_request_post(
                post_data,
                contact_group=GROUP_SERVICE_PROVIDERS,
                contact_subtype=SUBTYPE_STO,
            ),
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_STO,
        )
        detailing_criteria = validate_and_normalize_criteria(
            criteria_raw_from_request_post(
                post_data,
                contact_group=GROUP_SERVICE_PROVIDERS,
                contact_subtype=SUBTYPE_DETAILING,
            ),
            contact_group=GROUP_SERVICE_PROVIDERS,
            contact_subtype=SUBTYPE_DETAILING,
        )

        self.assertEqual(sto_criteria['services'], [sto_service.id])
        self.assertEqual(detailing_criteria['services'], [detailing_service.id])


class MarketingAudienceValidationTests(TestCase):
    def test_rejects_unknown_post_keys(self):
        with self.assertRaises(CriteriaValidationError):
            validate_and_normalize_criteria(
                {'primary_cities': ['Алматы'], 'evil_key': 'hack'},
                contact_group=GROUP_BUYERS,
                contact_subtype=SUBTYPE_PARTS_REQUESTS,
            )

    def test_rejects_invalid_date_range(self):
        normalized = normalize_marketing_criteria(
            {
                'activity_from': '2026-06-01',
                'activity_to': '2026-01-01',
            },
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
        )
        self.assertIsNone(normalized['activity_from'])
        self.assertIsNone(normalized['activity_to'])

    def test_manual_post_rejected_in_view(self):
        client = Client()
        user = User.objects.create_user('validator', password='secret', is_staff=True)
        grant_marketing_permission(user)
        client.login(username='validator', password='secret')
        response = client.post(
            reverse('marketing:audience_create'),
            data={
                'action': 'save',
                'contact_group': GROUP_BUYERS,
                'contact_subtype': SUBTYPE_PARTS_REQUESTS,
                'name': 'Bad criteria',
                'injected': 'value',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingAudience.objects.filter(name='Bad criteria').exists())


class MarketingAudienceViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('marketer', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='marketer', password='secret')

    def _create_audience(self, **kwargs) -> MarketingAudience:
        defaults = {
            'name': 'Test audience',
            'contact_group': GROUP_BUYERS,
            'contact_subtype': SUBTYPE_PARTS_REQUESTS,
            'criteria': {'primary_cities': ['Алматы']},
            'created_by': self.user,
        }
        defaults.update(kwargs)
        return MarketingAudience.objects.create(**defaults)

    def test_create_parts_audience_via_post(self):
        response = self.client.post(
            reverse('marketing:audience_create'),
            data={
                'action': 'save',
                'contact_group': GROUP_BUYERS,
                'contact_subtype': SUBTYPE_PARTS_REQUESTS,
                'name': 'Покупатели Алматы',
                'description': 'Test',
                'is_active': 'on',
                'primary_cities': ['Алматы'],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketingAudience.objects.filter(name='Покупатели Алматы').exists())

    def test_delete_requires_post_confirmation(self):
        audience = self._create_audience()
        response = self.client.post(
            reverse('marketing:audience_delete', kwargs={'pk': audience.pk}),
            data={'confirm': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MarketingAudience.objects.filter(pk=audience.pk).exists())

    def test_no_send_urls(self):
        urls = [
            reverse('marketing:audiences'),
            reverse('marketing:audience_create'),
        ]
        audience = self._create_audience()
        urls.extend([
            reverse('marketing:audience_detail', kwargs={'pk': audience.pk}),
            reverse('marketing:audience_edit', kwargs={'pk': audience.pk}),
        ])
        for url in urls:
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 302))
            self.assertNotIn('send_whatsapp', response.content.decode('utf-8').lower())
        calc_response = self.client.post(
            reverse('marketing:audience_calculate', kwargs={'pk': audience.pk}),
        )
        self.assertEqual(calc_response.status_code, 302)

    def test_detail_html_no_full_phone(self):
        buyer = make_buyer()
        grant_consent(buyer)
        audience = self._create_audience()
        response = self.client.get(reverse('marketing:audience_detail', kwargs={'pk': audience.pk}))
        self.assertNotIn(buyer.phone_normalized, response.content.decode('utf-8'))

    def test_calculate_updates_counts(self):
        make_buyer(primary_city='Алматы')
        audience = self._create_audience()
        response = self.client.post(reverse('marketing:audience_calculate', kwargs={'pk': audience.pk}))
        self.assertEqual(response.status_code, 302)
        audience.refresh_from_db()
        self.assertIsNotNone(audience.last_calculated_at)

    def test_edit_restores_criteria(self):
        buyer = make_buyer(primary_city='Алматы')
        BuyerVehicle.objects.create(
            buyer=buyer,
            transport_type='car',
            brand='Toyota',
            brand_normalized=normalize_buyer_text('Toyota'),
            model='Camry',
            model_normalized=normalize_buyer_text('Camry'),
        )
        audience = self._create_audience(criteria={'cities': ['Алматы'], 'brands': ['Toyota']})
        response = self.client.get(reverse('marketing:audience_edit', kwargs={'pk': audience.pk}))
        self.assertContains(response, 'value="Toyota"')
        self.assertContains(response, 'value="Алматы"')


class MarketingAudienceWizardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('wizard', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='wizard', password='secret')
        self.create_url = reverse('marketing:audience_create')
        self.sto_service = Service.objects.create(name='Диагностика')
        self.detailing_service = Service.objects.create(name='Полировка')
        ServiceSeller.objects.create(
            name='STO',
            whatsapp=next_phone(),
            city='Алматы',
            seller_type='sto',
            password='hash',
            is_active=True,
        ).services.add(self.sto_service)
        ServiceSeller.objects.create(
            name='Detail',
            whatsapp=next_phone(),
            city='Астана',
            seller_type='detailing',
            password='hash',
            is_active=True,
        ).services.add(self.detailing_service)

    def _get_step3(self, *, group: str, subtype: str, **extra):
        params = {
            'step': 3,
            'contact_group': group,
            'contact_subtype': subtype,
            'name': 'Wizard test',
            **extra,
        }
        return self.client.get(self.create_url, params)

    def test_step3_buyers_parts_requests_shows_buyer_filters(self):
        response = self._get_step3(group=GROUP_BUYERS, subtype=SUBTYPE_PARTS_REQUESTS)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Основной город', content)
        self.assertIn('Города поиска', content)
        self.assertIn('Область поиска', content)
        self.assertIn('Транспорт', content)
        self.assertIn('Марки', content)
        self.assertIn('Модели', content)
        self.assertIn('Категории запчастей', content)
        self.assertIn('Период интереса', content)

    def test_step3_sellers_request_sellers_shows_seller_filters(self):
        response = self._get_step3(group=GROUP_SELLERS, subtype=SUBTYPE_REQUEST_SELLERS)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Города', content)
        self.assertIn('Транспорт', content)
        self.assertIn('Категории запчастей', content)
        self.assertIn('Получает заявки', content)
        self.assertNotIn('Основной город', content)
        self.assertNotIn('Города поиска', content)

    def test_step3_sto_shows_sto_services_only(self):
        response = self._get_step3(group=GROUP_SERVICE_PROVIDERS, subtype=SUBTYPE_STO)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Конкретные услуги', content)
        self.assertIn('Диагностика', content)
        self.assertNotIn('Полировка', content)
        self.assertNotIn('Категории запчастей', content)
        self.assertNotIn('Основной город', content)

    def test_step3_detailing_shows_detailing_services_only(self):
        response = self._get_step3(group=GROUP_SERVICE_PROVIDERS, subtype=SUBTYPE_DETAILING)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Конкретные услуги', content)
        self.assertIn('Полировка', content)
        self.assertNotIn('Диагностика', content)
        self.assertNotIn('Категории запчастей', content)

    def test_invalid_group_subtype_returns_step_two_with_error(self):
        response = self._get_step3(group=GROUP_BUYERS, subtype=SUBTYPE_STO)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Подтип не соответствует выбранной группе')
        self.assertContains(response, 'Шаг 2. Подтип')
        content = response.content.decode('utf-8')
        self.assertIn('marketing-wizard-steps" data-step="2"', content)
        self.assertIn('marketing-wizard-steps is-hidden" data-step="3"', content)

    def test_step3_get_does_not_create_audience(self):
        before = MarketingAudience.objects.count()
        response = self._get_step3(group=GROUP_BUYERS, subtype=SUBTYPE_PARTS_REQUESTS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MarketingAudience.objects.count(), before)

    def test_step3_get_preserves_name_and_description(self):
        response = self._get_step3(
            group=GROUP_BUYERS,
            subtype=SUBTYPE_PARTS_REQUESTS,
            name='Saved name',
            description='Saved description',
        )
        self.assertContains(response, 'value="Saved name"')
        self.assertContains(response, 'Saved description')

    def test_step3_get_has_no_send_actions(self):
        response = self._get_step3(group=GROUP_SERVICE_PROVIDERS, subtype=SUBTYPE_STO)
        content = response.content.decode('utf-8').lower()
        self.assertNotIn('send_whatsapp', content)
        self.assertNotIn('buyerbroadcastrecipient', content)


class MarketingAudienceTestContactsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('test-audience', password='secret', is_staff=True)
        grant_marketing_permission(self.user)
        self.client.login(username='test-audience', password='secret')
        self.create_url = reverse('marketing:audience_create')

    def _get_step3(self, **extra):
        params = {
            'step': 3,
            'contact_group': GROUP_TEST,
            'contact_subtype': SUBTYPE_TEST_CONTACTS,
            'name': 'Тестовая аудитория',
            **extra,
        }
        return self.client.get(self.create_url, params)

    def test_step3_does_not_render_activity_or_is_test_fields(self):
        response = self._get_step3()
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertNotIn('name="activity_period"', content)
        self.assertNotIn('name="activity_from"', content)
        self.assertNotIn('name="activity_to"', content)
        self.assertNotIn('name="is_test"', content)
        self.assertIn(
            'В аудиторию входят только активные тестовые контакты с подтверждённым рекламным согласием.',
            content,
        )

    def test_create_test_contacts_audience_succeeds(self):
        response = self.client.post(
            self.create_url,
            data={
                'action': 'save',
                'contact_group': GROUP_TEST,
                'contact_subtype': SUBTYPE_TEST_CONTACTS,
                'name': 'Тестовые контакты',
                'description': 'Проверка',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        audience = MarketingAudience.objects.get(name='Тестовые контакты')
        self.assertEqual(audience.criteria, {})

    def test_create_rejects_manual_activity_fields(self):
        response = self.client.post(
            self.create_url,
            data={
                'action': 'save',
                'contact_group': GROUP_TEST,
                'contact_subtype': SUBTYPE_TEST_CONTACTS,
                'name': 'Bad test audience',
                'activity_period': 'last_30_days',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingAudience.objects.filter(name='Bad test audience').exists())

    def test_create_rejects_manual_is_test_false(self):
        response = self.client.post(
            self.create_url,
            data={
                'action': 'save',
                'contact_group': GROUP_TEST,
                'contact_subtype': SUBTYPE_TEST_CONTACTS,
                'name': 'Injected is_test',
                'is_test': 'false',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarketingAudience.objects.filter(name='Injected is_test').exists())

    def test_new_audience_is_active_checked_by_default(self):
        response = self.client.get(self.create_url)
        self.assertContains(response, 'name="is_active" checked')

    def test_calculation_includes_only_test_contacts(self):
        test_buyer = make_buyer(is_test_contact=True)
        grant_consent(test_buyer, CONTACT_CONSENT_STATUS_GRANTED)
        make_buyer(is_test_contact=False)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 1)

    def test_real_contact_not_in_test_audience(self):
        real_buyer = make_buyer(is_test_contact=False)
        grant_consent(real_buyer, CONTACT_CONSENT_STATUS_GRANTED)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.eligible_count, 0)

    def test_test_unknown_not_eligible(self):
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer, CONTACT_CONSENT_STATUS_UNKNOWN)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 0)

    def test_test_revoked_not_eligible(self):
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer, CONTACT_CONSENT_STATUS_REVOKED)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 0)

    def test_test_inactive_not_eligible(self):
        buyer = make_buyer(
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_BLOCKED,
        )
        grant_consent(buyer, CONTACT_CONSENT_STATUS_GRANTED)
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 0)

    def test_manual_is_test_false_criteria_ignored_for_calculation(self):
        test_buyer = make_buyer(is_test_contact=True)
        grant_consent(test_buyer, CONTACT_CONSENT_STATUS_GRANTED)
        make_buyer(is_test_contact=False)
        with self.assertRaises(CriteriaValidationError):
            validate_and_normalize_criteria(
                {'is_test': False},
                contact_group=GROUP_TEST,
                contact_subtype=SUBTYPE_TEST_CONTACTS,
            )
        result = calculate_audience(
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={'is_test': False},
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.eligible_count, 1)

    def test_detail_html_no_full_phone_for_test_audience(self):
        buyer = make_buyer(is_test_contact=True)
        grant_consent(buyer)
        audience = MarketingAudience.objects.create(
            name='Test detail',
            contact_group=GROUP_TEST,
            contact_subtype=SUBTYPE_TEST_CONTACTS,
            criteria={},
            created_by=self.user,
        )
        response = self.client.get(reverse('marketing:audience_detail', kwargs={'pk': audience.pk}))
        self.assertNotIn(buyer.phone_normalized, response.content.decode('utf-8'))

    def test_create_and_calculate_have_no_send_actions(self):
        urls = [self.create_url]
        response = self.client.post(
            self.create_url,
            data={
                'action': 'calculate',
                'contact_group': GROUP_TEST,
                'contact_subtype': SUBTYPE_TEST_CONTACTS,
                'name': 'Calc test',
            },
        )
        self.assertEqual(response.status_code, 200)
        urls.append(response.request['PATH_INFO'])
        content = response.content.decode('utf-8').lower()
        self.assertNotIn('send_whatsapp', content)
        for url in urls:
            page = self.client.get(url)
            self.assertNotIn('send_whatsapp', page.content.decode('utf-8').lower())
