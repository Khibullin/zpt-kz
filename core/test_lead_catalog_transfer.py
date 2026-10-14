import json
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from core.models import Seller, SellerLead
from core.services.lead_catalog_transfer import transfer_lead_to_catalog
from core.services.seller_lead_admin_workflow import WorkflowResultKind
from service_requests.models import ServiceSeller


class CatalogTransferTests(TestCase):
    def setUp(self):
        self.lead = SellerLead.objects.create(
            name='Test STO', whatsapp='77771234567', city='Алматы',
            source_url='https://2gis.kz/almaty/firm/123',
            whatsapp_source_url='https://2gis.kz/almaty/firm/123',
            notes='Internal review', profile_description='Ремонт двигателя',
            request_seller_transport_type='car',
        )

    def test_service_card_is_unclaimed_idempotent_and_keeps_evidence_private(self):
        first = transfer_lead_to_catalog(self.lead, target='sto')
        self.assertTrue(first.created_seller)
        seller = ServiceSeller.objects.get(pk=first.seller_id)
        self.assertTrue(seller.catalog_only)
        self.assertFalse(seller.receive_requests)
        self.assertIsNone(seller.user_id)
        self.assertEqual(seller.password, '')
        self.assertNotIn('Internal review', seller.description)
        self.assertIn('Internal review', seller.catalog_notes)
        self.assertEqual(seller.map_link, self.lead.source_url)
        second = transfer_lead_to_catalog(self.lead, target='sto')
        self.assertFalse(second.created_seller)
        self.assertEqual(ServiceSeller.objects.count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.service_seller_id, seller.pk)

    def test_service_card_cannot_login_even_with_matching_existing_user(self):
        get_user_model().objects.create_user(username=self.lead.whatsapp, password='Test-secret-123')
        transfer_lead_to_catalog(self.lead, target='sto')
        response = Client().post('/api/service/service-seller-login/',
            data=json.dumps({'whatsapp': self.lead.whatsapp, 'password': 'Test-secret-123'}),
            content_type='application/json')
        self.assertFalse(response.json().get('success', False))
        self.assertIsNone(ServiceSeller.objects.get().user_id)

    def test_existing_service_is_linked_without_overwriting_or_disabling(self):
        existing = ServiceSeller.objects.create(name='Original', whatsapp='+7 777 1234567',
            city='Алматы', seller_type='sto', password='existing-hash', receive_requests=True)
        result = transfer_lead_to_catalog(self.lead, target='sto')
        self.assertTrue(result.linked_existing_seller)
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Original')
        self.assertEqual(existing.password, 'existing-hash')
        self.assertTrue(existing.receive_requests)
        self.assertFalse(existing.catalog_only)

    def test_ambiguous_phone_is_rejected(self):
        for phone in ('+7 777 1234567', '87771234567'):
            ServiceSeller.objects.create(name='Existing', whatsapp=phone, city='Алматы', seller_type='sto')
        result = transfer_lead_to_catalog(self.lead, target='sto')
        self.assertEqual(result.kind, WorkflowResultKind.ERROR)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.service_seller_id)

    def test_missing_whatsapp_source_stays_found(self):
        self.lead.whatsapp_source_url = ''
        self.lead.save()
        result = transfer_lead_to_catalog(self.lead, target='sto')
        self.assertEqual(result.kind, WorkflowResultKind.ERROR)
        self.assertFalse(ServiceSeller.objects.exists())

    def test_dismantler_created_without_delivery_or_credentials(self):
        result = transfer_lead_to_catalog(self.lead, target='dismantler')
        seller = Seller.objects.get(pk=result.seller_id)
        self.assertTrue(seller.is_dismantler)
        self.assertTrue(seller.catalog_only)
        self.assertFalse(seller.receive_requests)
        self.assertEqual(seller.password_hash, '')
        self.assertIsNone(seller.user_id)

    def test_catalog_seller_does_not_auto_link_to_matching_user(self):
        from core.services.seller_identity import ensure_request_seller_for_user, SellerIdentityError
        transfer_lead_to_catalog(self.lead, target='seller')
        user = get_user_model().objects.create_user(username=self.lead.whatsapp, password='Test-secret-123')
        with self.assertRaises(SellerIdentityError):
            ensure_request_seller_for_user(user)
        self.assertIsNone(Seller.objects.get().user_id)

    def test_action_requires_destination_permission(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from core.admin import SellerLeadAdmin, transfer_leads_to_sto
        user = get_user_model().objects.create_user(username='staff', is_staff=True)
        request = RequestFactory().post('/')
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        transfer_leads_to_sto(SellerLeadAdmin(SellerLead, AdminSite()), request, SellerLead.objects.all())
        self.assertFalse(ServiceSeller.objects.exists())
