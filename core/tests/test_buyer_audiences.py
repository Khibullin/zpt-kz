from __future__ import annotations

from datetime import timedelta

from django.contrib.admin.sites import site as default_admin_site
from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from core.buyer_audience_admin_forms import (
    BuyerAudienceAdminForm,
    format_criteria_summary,
)
from core.models import (
    BUYER_CONTACT_STATUS_ACTIVE,
    BUYER_CONTACT_STATUS_BLOCKED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    CONTACT_CONSENT_STATUS_UNKNOWN,
    BuyerAudience,
    BuyerCategoryInterest,
    BuyerContact,
    BuyerVehicle,
    ContactConsent,
)
from core.services.buyer_audience_service import (
    AUDIENCE_ACTIVITY_LAST_7_DAYS,
    AUDIENCE_ACTIVITY_LAST_30_DAYS,
    AUDIENCE_ACTIVITY_NO_ACTIVITY_DATE,
    AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS,
    EMPTY_AUDIENCE_CRITERIA,
    build_buyer_audience_queryset,
    eligible_buyer_contacts,
    normalize_audience_criteria,
    preview_buyer_audience,
)


_phone_counter = 9000000


def make_buyer(**kwargs) -> BuyerContact:
    global _phone_counter
    kwargs.pop('_suffix', None)
    _phone_counter += 1
    defaults = {
        'phone_normalized': f'77{_phone_counter:09d}'[-11:],
        'status': BUYER_CONTACT_STATUS_ACTIVE,
    }
    defaults.update(kwargs)
    return BuyerContact.objects.create(**defaults)


class NormalizeAudienceCriteriaTests(TestCase):
    def test_empty_value_returns_default_structure(self):
        self.assertEqual(normalize_audience_criteria(None), EMPTY_AUDIENCE_CRITERIA)

    def test_unknown_keys_ignored(self):
        result = normalize_audience_criteria({
            'countries': ['Казахстан'],
            'unexpected': ['x'],
        })
        self.assertEqual(result['countries'], ['Казахстан'])
        self.assertNotIn('unexpected', result)

    def test_duplicate_list_values_removed(self):
        result = normalize_audience_criteria({
            'cities': ['Алматы', 'алматы', ' АЛМАТЫ '],
        })
        self.assertEqual(result['cities'], ['Алматы'])

    def test_brands_and_categories_normalized(self):
        result = normalize_audience_criteria({
            'brands': ['Toyota'],
            'categories': ['Ходовая часть'],
        })
        self.assertEqual(result['brands'], ['toyota'])
        self.assertEqual(result['categories'], ['ходовая часть'])

    def test_negative_request_count_rejected(self):
        result = normalize_audience_criteria({
            'request_count_min': -1,
            'request_count_max': 5,
        })
        self.assertIsNone(result['request_count_min'])
        self.assertEqual(result['request_count_max'], 5)

    def test_min_greater_than_max_cleared(self):
        result = normalize_audience_criteria({
            'request_count_min': 10,
            'request_count_max': 2,
        })
        self.assertIsNone(result['request_count_min'])
        self.assertIsNone(result['request_count_max'])


class BuildBuyerAudienceQuerysetTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.almaty_toyota = make_buyer(
            _suffix='1000001',
            primary_country='Казахстан',
            primary_city='Алматы',
            requests_count=2,
            last_request_at=self.now - timedelta(days=3),
            last_search_scope='city',
        )
        self.astana_honda = make_buyer(
            _suffix='1000002',
            primary_country='Казахстан',
            primary_city='Астана',
            requests_count=3,
            last_request_at=self.now - timedelta(days=40),
            last_search_scope='kazakhstan',
        )
        self.old_buyer = make_buyer(
            _suffix='1000003',
            primary_city='Шымкент',
            requests_count=60,
            last_request_at=self.now - timedelta(days=200),
        )
        BuyerVehicle.objects.create(
            buyer=self.almaty_toyota,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        BuyerVehicle.objects.create(
            buyer=self.almaty_toyota,
            transport_type='car',
            brand='Lexus',
            model='RX',
        )
        BuyerVehicle.objects.create(
            buyer=self.astana_honda,
            transport_type='truck',
            brand='Isuzu',
            model='NQR',
        )
        BuyerCategoryInterest.objects.create(
            buyer=self.almaty_toyota,
            category='Ходовая',
        )
        BuyerCategoryInterest.objects.create(
            buyer=self.astana_honda,
            category='Двигатель',
        )

    def test_empty_audience_matches_all(self):
        qs = build_buyer_audience_queryset({})
        self.assertEqual(qs.count(), 3)

    def test_country_filter(self):
        qs = build_buyer_audience_queryset({'countries': ['казахстан']})
        self.assertEqual(qs.count(), 2)

    def test_multiple_cities_use_or(self):
        qs = build_buyer_audience_queryset({'cities': ['Алматы', 'Астана']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {
            self.almaty_toyota.pk,
            self.astana_honda.pk,
        })

    def test_transport_type_filter(self):
        qs = build_buyer_audience_queryset({'transport_types': ['truck']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.astana_honda.pk})

    def test_brand_filter(self):
        qs = build_buyer_audience_queryset({'brands': ['toyota']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.almaty_toyota.pk})

    def test_model_filter(self):
        qs = build_buyer_audience_queryset({'models': ['camry']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.almaty_toyota.pk})

    def test_brand_and_model_must_match_same_vehicle(self):
        qs = build_buyer_audience_queryset({
            'brands': ['toyota'],
            'models': ['rx'],
        })
        self.assertEqual(qs.count(), 0)

    def test_category_filter(self):
        qs = build_buyer_audience_queryset({'categories': ['ходовая']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.almaty_toyota.pk})

    def test_search_scope_filter(self):
        qs = build_buyer_audience_queryset({'search_scopes': ['kazakhstan']})
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.astana_honda.pk})

    def test_activity_last_7_days(self):
        qs = build_buyer_audience_queryset({
            'activity_period': AUDIENCE_ACTIVITY_LAST_7_DAYS,
        })
        self.assertIn(self.almaty_toyota.pk, qs.values_list('pk', flat=True))
        self.assertNotIn(self.astana_honda.pk, qs.values_list('pk', flat=True))

    def test_activity_older_than_180_days(self):
        qs = build_buyer_audience_queryset({
            'activity_period': AUDIENCE_ACTIVITY_OLDER_THAN_180_DAYS,
        })
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.old_buyer.pk})

    def test_request_count_range(self):
        qs = build_buyer_audience_queryset({
            'request_count_min': 2,
            'request_count_max': 4,
        })
        self.assertEqual(set(qs.values_list('pk', flat=True)), {
            self.almaty_toyota.pk,
            self.astana_honda.pk,
        })

    def test_related_filters_do_not_duplicate(self):
        qs = build_buyer_audience_queryset({'categories': ['ходовая']})
        self.assertEqual(qs.count(), qs.distinct().count())


class PreviewBuyerAudienceTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.test_blocked = make_buyer(
            _suffix='2000002',
            is_test_contact=True,
            status=BUYER_CONTACT_STATUS_BLOCKED,
            requests_count=1,
            last_request_at=self.now,
        )
        self.blocked = make_buyer(
            _suffix='2000003',
            status=BUYER_CONTACT_STATUS_BLOCKED,
            requests_count=1,
            last_request_at=self.now,
        )
        self.granted = make_buyer(
            _suffix='2000004',
            requests_count=1,
            last_request_at=self.now,
        )
        self.unknown = make_buyer(
            _suffix='2000005',
            requests_count=1,
            last_request_at=self.now,
        )
        self.revoked = make_buyer(
            _suffix='2000006',
            requests_count=1,
            last_request_at=self.now,
        )
        self.missing = make_buyer(
            _suffix='2000007',
            requests_count=1,
            last_request_at=self.now,
        )
        ContactConsent.objects.create(
            buyer=self.granted,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=self.now,
        )
        ContactConsent.objects.create(
            buyer=self.unknown,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_UNKNOWN,
        )
        ContactConsent.objects.create(
            buyer=self.revoked,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_REVOKED,
            revoked_at=self.now,
        )

    def _preview(self, criteria=None):
        audience = BuyerAudience.objects.create(
            name='Test audience',
            criteria=criteria or {},
        )
        return preview_buyer_audience(audience)

    def test_test_contact_excluded(self):
        preview = self._preview()
        self.assertEqual(preview.excluded_test_count, 1)

    def test_blocked_contact_excluded(self):
        preview = self._preview()
        self.assertEqual(preview.excluded_status_count, 1)

    def test_test_and_blocked_not_double_counted(self):
        preview = self._preview()
        self.assertEqual(preview.excluded_test_count, 1)
        self.assertEqual(preview.excluded_status_count, 1)

    def test_marketing_granted_in_final(self):
        preview = self._preview()
        self.assertEqual(preview.final_recipient_count, 1)

    def test_marketing_unknown_not_in_final(self):
        preview = self._preview()
        self.assertEqual(preview.marketing_unknown_count, 1)
        self.assertEqual(preview.final_recipient_count, 1)

    def test_marketing_revoked_not_in_final(self):
        preview = self._preview()
        self.assertEqual(preview.marketing_revoked_count, 1)
        self.assertEqual(preview.final_recipient_count, 1)

    def test_missing_marketing_counted(self):
        preview = self._preview()
        self.assertEqual(preview.marketing_missing_count, 1)

    def test_sample_limited_to_50(self):
        for index in range(55):
            buyer = make_buyer(_suffix=f'3{index:06d}', requests_count=1)
            ContactConsent.objects.create(
                buyer=buyer,
                channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
                purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
                status=CONTACT_CONSENT_STATUS_GRANTED,
                consented_at=self.now,
            )
        preview = self._preview()
        self.assertLessEqual(len(preview.sample_contacts), 50)

    def test_sample_masks_phone(self):
        preview = self._preview()
        for contact in preview.sample_contacts:
            self.assertIn('***', contact.masked_phone)
            self.assertNotEqual(contact.masked_phone, self.granted.phone_normalized)

    def test_eligible_buyer_contacts_unchanged(self):
        buyer = make_buyer(_suffix='4000001')
        self.assertIn(buyer.pk, eligible_buyer_contacts().values_list('pk', flat=True))


class BuyerAudienceFormAdminTests(TestCase):
    def setUp(self):
        self.buyer = make_buyer(
            _suffix='5000001',
            primary_country='Казахстан',
            primary_city='Алматы',
        )
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='Toyota',
            model='Camry',
        )
        BuyerCategoryInterest.objects.create(
            buyer=self.buyer,
            category='Ходовая',
        )
        self.audience = BuyerAudience.objects.create(
            name='Saved audience',
            criteria={
                'cities': ['Алматы'],
                'brands': ['toyota'],
                'categories': ['ходовая'],
                'activity_period': AUDIENCE_ACTIVITY_LAST_30_DAYS,
                'request_count_min': 1,
                'request_count_max': 4,
            },
        )
        self.admin = default_admin_site._registry[BuyerAudience]
        self.client = Client()

    def test_form_loads_existing_criteria(self):
        form = BuyerAudienceAdminForm(instance=self.audience)
        self.assertEqual(form.initial.get('cities'), ['Алматы'])
        self.assertEqual(form.initial.get('brands'), ['toyota'])

    def test_form_saves_criteria(self):
        form = BuyerAudienceAdminForm(
            data={
                'name': 'New audience',
                'description': '',
                'is_active': True,
                'countries': ['Казахстан'],
                'cities': ['Алматы'],
                'transport_types': ['car'],
                'brands': ['toyota'],
                'models': ['camry'],
                'categories': ['ходовая'],
                'search_scopes': ['city'],
                'activity_period': AUDIENCE_ACTIVITY_LAST_7_DAYS,
                'request_count_min': 1,
                'request_count_max': 3,
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        audience = form.save()
        self.assertEqual(audience.criteria['brands'], ['toyota'])
        self.assertEqual(audience.criteria['request_count_max'], 3)

    def test_form_rejects_min_greater_than_max(self):
        form = BuyerAudienceAdminForm(
            data={
                'name': 'Invalid audience',
                'description': '',
                'is_active': True,
                'request_count_min': 5,
                'request_count_max': 2,
            },
        )
        self.assertFalse(form.is_valid())

    def test_brand_choices_not_duplicated_by_case(self):
        BuyerVehicle.objects.create(
            buyer=self.buyer,
            transport_type='car',
            brand='TOYOTA',
            model='Corolla',
        )
        form = BuyerAudienceAdminForm()
        brand_values = [value for value, _ in form.fields['brands'].choices]
        self.assertEqual(brand_values.count('toyota'), 1)

    def test_criteria_summary(self):
        self.assertIn('Алматы', format_criteria_summary(self.audience.criteria))

    def test_preview_link_builds_admin_url(self):
        link = default_admin_site._registry[BuyerAudience].preview_link(self.audience)
        self.assertIn('preview', link)

    def test_preview_requires_login(self):
        url = reverse('admin:core_buyeraudience_preview', args=[self.audience.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_preview_denied_without_view_permission(self):
        user = User.objects.create_user(username='viewerless', password='pass')
        self.client.force_login(user)
        url = reverse('admin:core_buyeraudience_preview', args=[self.audience.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_preview_shows_statistics_for_staff(self):
        user = User.objects.create_superuser(
            username='adminuser',
            password='pass',
            email='admin@example.com',
        )
        self.client.force_login(user)
        url = reverse('admin:core_buyeraudience_preview', args=[self.audience.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Найдено по критериям')
        self.assertContains(response, 'marketing consent = granted')

    def test_preview_does_not_expose_full_phone(self):
        user = User.objects.create_superuser(
            username='adminuser2',
            password='pass',
            email='admin2@example.com',
        )
        ContactConsent.objects.create(
            buyer=self.buyer,
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            status=CONTACT_CONSENT_STATUS_GRANTED,
            consented_at=timezone.now(),
        )
        self.client.force_login(user)
        url = reverse('admin:core_buyeraudience_preview', args=[self.audience.pk])
        response = self.client.get(url)
        self.assertNotContains(response, self.buyer.phone_normalized)

    def test_preview_does_not_change_consents(self):
        user = User.objects.create_superuser(
            username='adminuser3',
            password='pass',
            email='admin3@example.com',
        )
        before = ContactConsent.objects.count()
        self.client.force_login(user)
        self.client.get(
            reverse('admin:core_buyeraudience_preview', args=[self.audience.pk]),
        )
        self.assertEqual(ContactConsent.objects.count(), before)
