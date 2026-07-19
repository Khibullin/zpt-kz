from __future__ import annotations

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from catalog.models import SellerProfile
from core.admin import (
    SellerLeadAdmin,
    convert_seller_leads_to_both,
    convert_seller_leads_to_request_sellers,
    mark_seller_leads_marketplace_planned,
    reject_seller_leads,
    return_seller_leads_to_review,
)
from core.models import Seller, SellerLead, SellerLeadContactCandidate
from core.services.seller_lead_admin_workflow import (
    WorkflowResultKind,
    convert_lead_and_mark_marketplace_planned,
    convert_lead_to_request_seller,
    mark_marketplace_invitation_planned,
    reject_lead,
    return_lead_to_review,
)
from core.services.seller_lead_contact_search import enrich_seller_lead_contacts


FAKE_WHATSAPP_A = '77009990001'
FAKE_WHATSAPP_B = '77009990002'
FAKE_WHATSAPP_C = '77009990003'
FAKE_WHATSAPP_D = '77009990004'
FAKE_WHATSAPP_E = '77009990005'
FAKE_WHATSAPP_F = '77009990006'
FAKE_USERNAME_A = 'test_lead_shop_a'
FAKE_USERNAME_B = 'test_lead_shop_b'
FAKE_USERNAME_C = 'test_lead_shop_c'
FAKE_USERNAME_D = 'test_lead_shop_d'


def _make_lead(**kwargs) -> SellerLead:
    defaults = {
        'name': 'Test Lead Shop',
        'instagram_username': FAKE_USERNAME_A,
        'city': 'Test City',
        'category': 'Test Category',
    }
    defaults.update(kwargs)
    return SellerLead.objects.create(**defaults)


def _make_convertible_lead(**kwargs) -> SellerLead:
    defaults = {'request_seller_transport_type': 'car'}
    defaults.update(kwargs)
    return _make_lead(**defaults)


class SellerLeadAdminWorkflowTests(TestCase):
    def test_new_seller_lead_has_needs_review(self):
        lead = _make_lead()
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)

    def test_valid_whatsapp_creates_request_seller(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_A)
        result = convert_lead_to_request_seller(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.SUCCESS)
        self.assertTrue(result.created_seller)
        self.assertIsNotNone(lead.request_seller_id)
        self.assertEqual(Seller.objects.filter(whatsapp=FAKE_WHATSAPP_A).count(), 1)

    def test_created_seller_fields_mapped_correctly(self):
        lead = _make_convertible_lead(
            name='Mapped Shop Name',
            whatsapp=FAKE_WHATSAPP_B,
            instagram_username=FAKE_USERNAME_B,
            city='Mapped City',
            source_url='https://example.test/source',
            request_seller_transport_type='car',
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        seller = lead.request_seller

        self.assertEqual(seller.name, 'Mapped Shop Name')
        self.assertEqual(seller.whatsapp, FAKE_WHATSAPP_B)
        self.assertEqual(seller.city, 'Mapped City')
        self.assertEqual(seller.transport_type, 'car')
        self.assertFalse(seller.receive_requests)
        self.assertIn(f'@{FAKE_USERNAME_B}', seller.notes)
        self.assertIn('https://example.test/source', seller.notes)

    def test_repeat_convert_does_not_create_duplicate_seller(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_A, instagram_username=FAKE_USERNAME_A)
        convert_lead_to_request_seller(lead)
        convert_lead_to_request_seller(lead)

        self.assertEqual(Seller.objects.filter(whatsapp=FAKE_WHATSAPP_A).count(), 1)

    def test_existing_seller_by_whatsapp_is_reused(self):
        existing = Seller.objects.create(
            name='Existing Seller',
            whatsapp=FAKE_WHATSAPP_C,
            transport_type='car',
        )
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_C,
            instagram_username=FAKE_USERNAME_C,
        )
        result = convert_lead_to_request_seller(lead)

        self.assertEqual(result.kind, WorkflowResultKind.WARNING)
        self.assertTrue(result.linked_existing_seller)
        self.assertEqual(Seller.objects.filter(whatsapp=FAKE_WHATSAPP_C).count(), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller_id, existing.pk)

    def test_seller_lead_links_to_existing_seller(self):
        existing = Seller.objects.create(
            name='Linked Seller',
            whatsapp=FAKE_WHATSAPP_D,
            transport_type='car',
        )
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_D,
            instagram_username=FAKE_USERNAME_D,
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller_id, existing.pk)

    def test_lead_without_whatsapp_is_not_converted(self):
        lead = _make_lead(whatsapp='')
        result = convert_lead_to_request_seller(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.WARNING)
        self.assertIsNone(lead.request_seller_id)
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)

    def test_conflict_candidates_without_primary_block_conversion(self):
        lead = _make_lead(whatsapp='', instagram_username='conflict_shop')
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value=FAKE_WHATSAPP_A,
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value=FAKE_WHATSAPP_B,
            confidence='medium',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        result = convert_lead_to_request_seller(lead)

        self.assertEqual(result.kind, WorkflowResultKind.ERROR)
        self.assertIn('конфликт', result.message.lower())
        self.assertIsNone(lead.request_seller_id)

    def test_marketplace_planned_does_not_create_marketplace_account(self):
        lead = _make_lead(whatsapp='')
        mark_marketplace_invitation_planned(lead)
        self.assertEqual(SellerProfile.objects.count(), 0)

    def test_marketplace_planned_sets_correct_workflow(self):
        lead = _make_lead(whatsapp='')
        mark_marketplace_invitation_planned(lead)
        lead.refresh_from_db()

        self.assertEqual(
            lead.marketplace_invitation_status,
            SellerLead.MARKETPLACE_INVITATION_PLANNED,
        )
        self.assertEqual(lead.review_status, SellerLead.REVIEW_MARKETPLACE_PLANNED)
        self.assertIsNotNone(lead.marketplace_invitation_planned_at)

    def test_repeat_marketplace_planned_is_idempotent(self):
        lead = _make_lead(whatsapp='')
        first = mark_marketplace_invitation_planned(lead)
        planned_at = lead.marketplace_invitation_planned_at
        second = mark_marketplace_invitation_planned(lead)
        lead.refresh_from_db()

        self.assertEqual(first.kind, WorkflowResultKind.SUCCESS)
        self.assertEqual(second.kind, WorkflowResultKind.WARNING)
        self.assertEqual(lead.marketplace_invitation_planned_at, planned_at)

    def test_both_directions_action_works(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='both_shop',
        )
        result = convert_lead_and_mark_marketplace_planned(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.SUCCESS)
        self.assertIsNotNone(lead.request_seller_id)
        self.assertEqual(
            lead.review_status,
            SellerLead.REVIEW_CONVERTED_AND_MARKETPLACE_PLANNED,
        )

    def test_both_directions_does_not_marketplace_on_conversion_error(self):
        lead = _make_lead(whatsapp='', instagram_username='both_fail_shop')
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value=FAKE_WHATSAPP_A,
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        result = convert_lead_and_mark_marketplace_planned(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.ERROR)
        self.assertEqual(lead.marketplace_invitation_status, '')
        self.assertIsNone(lead.request_seller_id)

    def test_reject_does_not_delete_seller_lead(self):
        lead = _make_lead(whatsapp=FAKE_WHATSAPP_A)
        lead_id = lead.pk
        reject_lead(lead)
        self.assertTrue(SellerLead.objects.filter(pk=lead_id).exists())

    def test_reject_does_not_delete_working_seller(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_B, instagram_username='reject_shop')
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        seller_id = lead.request_seller_id

        reject_lead(lead)
        self.assertTrue(Seller.objects.filter(pk=seller_id).exists())

    def test_repeat_reject_is_safe(self):
        lead = _make_lead(instagram_username='repeat_reject_shop')
        first = reject_lead(lead)
        rejected_at = lead.rejected_at
        second = reject_lead(lead)
        lead.refresh_from_db()

        self.assertEqual(first.kind, WorkflowResultKind.SUCCESS)
        self.assertEqual(second.kind, WorkflowResultKind.WARNING)
        self.assertEqual(lead.rejected_at, rejected_at)

    def test_return_to_review_does_not_delete_seller(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_C, instagram_username='return_shop')
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        seller_id = lead.request_seller_id

        return_lead_to_review(lead)
        self.assertTrue(Seller.objects.filter(pk=seller_id).exists())
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller_id, seller_id)

    def test_return_to_review_does_not_delete_whatsapp(self):
        lead = _make_lead(whatsapp=FAKE_WHATSAPP_D, instagram_username='return_wa_shop')
        reject_lead(lead)
        return_lead_to_review(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, FAKE_WHATSAPP_D)

    def test_return_to_review_does_not_delete_candidates(self):
        lead = _make_lead(whatsapp='', instagram_username='return_candidate_shop')
        candidate = SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            value=FAKE_WHATSAPP_A,
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        )
        reject_lead(lead)
        return_lead_to_review(lead)
        self.assertTrue(
            SellerLeadContactCandidate.objects.filter(pk=candidate.pk).exists(),
        )

    @override_settings(
        SELLER_SEARCH_PROVIDER='brave',
        BRAVE_SEARCH_API_KEY='test-key',
        SELLER_SEARCH_ENABLED=True,
    )
    def test_pipeline_enrichment_does_not_reset_workflow(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='pipeline_guard_shop',
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        seller_id = lead.request_seller_id
        review_status = lead.review_status
        lead.whatsapp = ''
        lead.status = SellerLead.STATUS_NEEDS_REVIEW
        lead.save(update_fields=['whatsapp', 'status', 'updated_at'])

        class FakeClient:
            def search(self, query, count=10):
                return [{
                    'title': 'Pipeline Guard Shop',
                    'url': f'https://wa.me/{FAKE_WHATSAPP_B}',
                    'description': 'WhatsApp Business',
                }]

        enrich_seller_lead_contacts(
            lead_ids=[lead.pk],
            max_queries_per_lead=1,
            dry_run=False,
            client=FakeClient(),
        )
        lead.refresh_from_db()

        self.assertEqual(lead.request_seller_id, seller_id)
        self.assertEqual(lead.review_status, review_status)
        self.assertEqual(lead.whatsapp, FAKE_WHATSAPP_B)

    def test_cron_created_seller_lead_gets_needs_review(self):
        lead = SellerLead.objects.create(
            name='Cron Shop',
            instagram_username='cron_shop',
            instagram_url='https://www.instagram.com/cron_shop/',
            city='Test City',
            category='Test Category',
            source_type='web_search',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)

    def _admin_request(self):
        request = RequestFactory().get('/admin/core/sellerlead/')
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        request.user = get_user_model().objects.get_or_create(
            username='admin_workflow_test',
            defaults={
                'email': 'admin-workflow-test@example.test',
                'is_staff': True,
                'is_superuser': True,
            },
        )[0]
        if not request.user.has_usable_password():
            request.user.set_password('test-password-not-used')
            request.user.save(update_fields=['password'])
        return request

    def test_admin_bulk_action_processes_multiple_leads(self):
        lead_a = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='bulk_a',
            name='Bulk A',
        )
        lead_b = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_B,
            instagram_username='bulk_b',
            name='Bulk B',
            request_seller_transport_type='truck',
        )
        request = self._admin_request()
        queryset = SellerLead.objects.filter(pk__in=[lead_a.pk, lead_b.pk])

        convert_seller_leads_to_request_sellers(SellerLeadAdmin(SellerLead, AdminSite()), request, queryset)

        lead_a.refresh_from_db()
        lead_b.refresh_from_db()
        self.assertIsNotNone(lead_a.request_seller_id)
        self.assertIsNotNone(lead_b.request_seller_id)

    def test_mixed_bulk_action_success_and_warning(self):
        lead_ok = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_C,
            instagram_username='bulk_ok',
        )
        lead_missing = _make_lead(
            whatsapp='',
            instagram_username='bulk_missing',
        )
        request = self._admin_request()
        queryset = SellerLead.objects.filter(pk__in=[lead_ok.pk, lead_missing.pk])

        convert_seller_leads_to_request_sellers(SellerLeadAdmin(SellerLead, AdminSite()), request, queryset)

        lead_ok.refresh_from_db()
        lead_missing.refresh_from_db()
        self.assertIsNotNone(lead_ok.request_seller_id)
        self.assertIsNone(lead_missing.request_seller_id)

    def test_pipeline_status_field_not_broken_by_conversion(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_D,
            instagram_username='pipeline_status_shop',
            status=SellerLead.STATUS_NEEDS_REVIEW,
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.status, SellerLead.STATUS_NEEDS_REVIEW)

    def test_marketplace_seller_not_auto_created_on_conversion(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_A, instagram_username='no_market_shop')
        convert_lead_and_mark_marketplace_planned(lead)
        self.assertEqual(SellerProfile.objects.count(), 0)

    def test_passwords_not_auto_generated(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_B, instagram_username='no_password_shop')
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller.password_hash, '')

    @patch('core.whatsapp_template_sender.send_whatsapp_template_message')
    def test_no_whatsapp_messages_sent_by_actions(self, mock_send):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_C, instagram_username='no_wa_send')
        convert_lead_to_request_seller(lead)
        mark_marketplace_invitation_planned(lead)
        reject_lead(lead)
        mock_send.assert_not_called()

    @patch('django.core.mail.send_mail')
    def test_no_email_sent_to_discovered_sellers(self, mock_send_mail):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_D, instagram_username='no_email_shop')
        convert_lead_to_request_seller(lead)
        mark_marketplace_invitation_planned(lead)
        reject_lead(lead)
        mock_send_mail.assert_not_called()

    def test_admin_list_display_and_filters_do_not_crash(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='admin_ui_shop',
        )
        convert_lead_to_request_seller(lead)
        admin = SellerLeadAdmin(SellerLead, AdminSite())
        request = self._admin_request()

        changelist = admin.get_changelist_instance(request)
        queryset = changelist.get_queryset(request)
        rendered = admin.get_list_display(request)
        self.assertIn('review_status', rendered)
        self.assertTrue(queryset.filter(pk=lead.pk).exists())

        for filter_spec in admin.get_list_filter(request):
            if isinstance(filter_spec, str):
                continue
            filter_instance = filter_spec(request, {}, queryset, admin)
            filter_instance.lookups(request, admin)
            filter_instance.queryset(request, queryset)

    def test_request_seller_deletion_sets_null_on_seller_lead(self):
        lead = _make_convertible_lead(whatsapp=FAKE_WHATSAPP_B, instagram_username='fk_shop')
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        seller = lead.request_seller
        seller.delete()
        lead.refresh_from_db()
        self.assertIsNone(lead.request_seller_id)

    def test_marketplace_only_without_whatsapp_is_allowed(self):
        lead = _make_lead(whatsapp='', instagram_username='market_only_shop')
        result = mark_marketplace_invitation_planned(lead)
        lead.refresh_from_db()
        self.assertEqual(result.kind, WorkflowResultKind.SUCCESS)
        self.assertEqual(lead.review_status, SellerLead.REVIEW_MARKETPLACE_PLANNED)

    def test_admin_reject_and_return_actions(self):
        lead = _make_lead(instagram_username='admin_reject_shop')
        request = self._admin_request()
        queryset = SellerLead.objects.filter(pk=lead.pk)

        reject_seller_leads(SellerLeadAdmin(SellerLead, AdminSite()), request, queryset)
        lead.refresh_from_db()
        self.assertEqual(lead.review_status, SellerLead.REVIEW_REJECTED)

        return_seller_leads_to_review(SellerLeadAdmin(SellerLead, AdminSite()), request, queryset)
        lead.refresh_from_db()
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)

    def test_admin_marketplace_and_both_actions(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='admin_both_shop',
        )
        request = self._admin_request()

        mark_seller_leads_marketplace_planned(
            SellerLeadAdmin(SellerLead, AdminSite()),
            request,
            SellerLead.objects.filter(pk=lead.pk),
        )
        lead.refresh_from_db()
        self.assertEqual(
            lead.marketplace_invitation_status,
            SellerLead.MARKETPLACE_INVITATION_PLANNED,
        )

        convert_seller_leads_to_both(
            SellerLeadAdmin(SellerLead, AdminSite()),
            request,
            SellerLead.objects.filter(pk=lead.pk),
        )
        lead.refresh_from_db()
        self.assertEqual(
            lead.review_status,
            SellerLead.REVIEW_CONVERTED_AND_MARKETPLACE_PLANNED,
        )

    def test_new_seller_not_created_without_transport_type(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='no_transport_shop',
        )
        result = convert_lead_to_request_seller(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.WARNING)
        self.assertIsNone(lead.request_seller_id)
        self.assertFalse(Seller.objects.filter(whatsapp=FAKE_WHATSAPP_E).exists())

    def test_missing_transport_type_shows_warning_message(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_F,
            instagram_username='transport_warning_shop',
        )
        result = convert_lead_to_request_seller(lead)

        self.assertIn('тип транспорта', result.message.lower())

    def test_review_status_unchanged_when_transport_type_missing(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='transport_review_shop',
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)

    def test_both_directions_without_transport_type_does_not_marketplace_planned(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_F,
            instagram_username='both_no_transport_shop',
        )
        result = convert_lead_and_mark_marketplace_planned(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.WARNING)
        self.assertEqual(lead.marketplace_invitation_status, '')
        self.assertIsNone(lead.marketplace_invitation_planned_at)
        self.assertIsNone(lead.request_seller_id)

    def test_car_creates_seller_with_transport_type_car(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='car_transport_shop',
            request_seller_transport_type='car',
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller.transport_type, 'car')

    def test_truck_creates_seller_with_transport_type_truck(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_F,
            instagram_username='truck_transport_shop',
            request_seller_transport_type='truck',
        )
        convert_lead_to_request_seller(lead)
        lead.refresh_from_db()
        self.assertEqual(lead.request_seller.transport_type, 'truck')

    def test_no_automatic_car_fallback_without_transport_type(self):
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='no_car_fallback_shop',
        )
        convert_lead_to_request_seller(lead)
        self.assertEqual(Seller.objects.filter(whatsapp=FAKE_WHATSAPP_E).count(), 0)

    def test_existing_seller_links_without_lead_transport_type(self):
        existing = Seller.objects.create(
            name='Truck Existing Seller',
            whatsapp=FAKE_WHATSAPP_E,
            transport_type='truck',
        )
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='link_no_transport_shop',
            request_seller_transport_type='',
        )
        result = convert_lead_to_request_seller(lead)
        lead.refresh_from_db()

        self.assertEqual(result.kind, WorkflowResultKind.WARNING)
        self.assertEqual(lead.request_seller_id, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.transport_type, 'truck')

    def test_existing_seller_transport_type_not_changed_by_lead_selection(self):
        existing = Seller.objects.create(
            name='Existing Truck Seller',
            whatsapp=FAKE_WHATSAPP_F,
            transport_type='truck',
        )
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_F,
            instagram_username='keep_truck_shop',
            request_seller_transport_type='car',
        )
        convert_lead_to_request_seller(lead)
        existing.refresh_from_db()
        self.assertEqual(existing.transport_type, 'truck')

    def test_both_directions_allows_marketplace_when_existing_seller_linked(self):
        existing = Seller.objects.create(
            name='Existing For Both',
            whatsapp=FAKE_WHATSAPP_E,
            transport_type='truck',
        )
        lead = _make_lead(
            whatsapp=FAKE_WHATSAPP_E,
            instagram_username='both_existing_shop',
            request_seller_transport_type='',
        )
        result = convert_lead_and_mark_marketplace_planned(lead)
        lead.refresh_from_db()

        self.assertIn(result.kind, (WorkflowResultKind.SUCCESS, WorkflowResultKind.WARNING))
        self.assertEqual(lead.request_seller_id, existing.pk)
        self.assertEqual(
            lead.marketplace_invitation_status,
            SellerLead.MARKETPLACE_INVITATION_PLANNED,
        )

    def test_return_to_review_clears_marketplace_planned_invariant(self):
        lead = _make_convertible_lead(
            whatsapp=FAKE_WHATSAPP_A,
            instagram_username='return_marketplace_shop',
        )
        convert_lead_and_mark_marketplace_planned(lead)
        return_lead_to_review(lead)
        lead.refresh_from_db()

        self.assertEqual(lead.marketplace_invitation_status, '')
        self.assertIsNone(lead.marketplace_invitation_planned_at)
        self.assertEqual(lead.review_status, SellerLead.REVIEW_NEEDS_REVIEW)
        self.assertIsNotNone(lead.request_seller_id)
