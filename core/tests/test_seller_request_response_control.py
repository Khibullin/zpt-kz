from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.admin import RequestAdmin, RequestDispatchAdmin
from core.models import (
    Request,
    RequestDispatch,
    Seller,
    SellerRequestAccess,
    SellerRequestPageEvent,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
)
from core.services.seller_request_response import (
    REACTION_CONTACT,
    REACTION_NO_REACTION,
    REACTION_OPENED,
    classify_seller_request_reaction,
    summarize_request_reactions,
)


class SellerRequestResponseServiceTests(TestCase):
    def test_classification_priority(self):
        self.assertEqual(
            classify_seller_request_reaction(
                dispatch_status=RequestDispatch.STATUS_SENT,
                event_types=[],
            ).code,
            REACTION_NO_REACTION,
        )
        self.assertEqual(
            classify_seller_request_reaction(
                dispatch_status=RequestDispatch.STATUS_SENT,
                event_types=[SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN],
            ).code,
            REACTION_OPENED,
        )
        self.assertEqual(
            classify_seller_request_reaction(
                dispatch_status=RequestDispatch.STATUS_SENT,
                event_types=[
                    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
                    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
                ],
            ).code,
            REACTION_CONTACT,
        )
        self.assertEqual(
            classify_seller_request_reaction(
                dispatch_status=RequestDispatch.STATUS_SENT,
                event_types=[
                    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
                    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
                    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
                ],
            ).label,
            'Нет в наличии',
        )
        self.assertEqual(
            classify_seller_request_reaction(
                dispatch_status=RequestDispatch.STATUS_QUEUED,
                event_types=[],
            ).label,
            'В очереди',
        )


class SellerRequestResponseAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username='response-admin',
            email='response-admin@example.test',
            password='AdminPass123!',
        )
        self.factory = RequestFactory()
        self.req = Request.objects.create(
            transport_type='car',
            brand='Toyota',
            model='Camry',
            category='Тормозная система',
            city='Алматы',
            phone='77001112233',
            status='sent',
        )
        self.silent_seller = Seller.objects.create(
            name='Silent Seller',
            whatsapp='77015550401',
            transport_type='car',
        )
        self.contact_seller = Seller.objects.create(
            name='Contact Seller',
            whatsapp='77015550402',
            transport_type='car',
        )
        self.declined_seller = Seller.objects.create(
            name='Declined Seller',
            whatsapp='77015550403',
            transport_type='car',
        )
        self.queued_seller = Seller.objects.create(
            name='Queued Seller',
            whatsapp='77015550404',
            transport_type='car',
        )
        now = timezone.now()
        self.silent_dispatch = RequestDispatch.objects.create(
            request=self.req,
            seller=self.silent_seller,
            wave_number=1,
            position_number=1,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=now,
            sent_at=now,
        )
        self.contact_dispatch = RequestDispatch.objects.create(
            request=self.req,
            seller=self.contact_seller,
            wave_number=1,
            position_number=2,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=now,
            sent_at=now,
        )
        self.declined_dispatch = RequestDispatch.objects.create(
            request=self.req,
            seller=self.declined_seller,
            wave_number=1,
            position_number=3,
            status=RequestDispatch.STATUS_SENT,
            scheduled_at=now,
            sent_at=now,
        )
        RequestDispatch.objects.create(
            request=self.req,
            seller=self.queued_seller,
            wave_number=2,
            position_number=4,
            status=RequestDispatch.STATUS_QUEUED,
            scheduled_at=now + timedelta(minutes=5),
        )
        self._event(
            self.contact_seller,
            SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
            token='contact-open',
        )
        self._event(
            self.contact_seller,
            SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
            token='contact-wa',
        )
        self._event(
            self.declined_seller,
            SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
            token='declined-open',
        )
        self._event(
            self.declined_seller,
            SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
            token='declined-outcome',
        )

    def _event(self, seller, event_type, *, token):
        access = SellerRequestAccess.objects.create(
            token=token,
            request=self.req,
            seller=seller,
            expires_at=timezone.now() + timedelta(hours=2),
        )
        return SellerRequestPageEvent.objects.create(
            access=access,
            request=self.req,
            seller=seller,
            event_type=event_type,
        )

    def _admin_request(self):
        request = self.factory.get('/admin/core/requestdispatch/')
        request.user = self.admin_user
        return request

    def test_request_summary_counts_real_reactions(self):
        obj = (
            RequestAdmin(Request, admin.site)
            .get_queryset(self._admin_request())
            .get(pk=self.req.pk)
        )

        summary = summarize_request_reactions(obj)

        self.assertEqual(summary.sent, 3)
        self.assertEqual(summary.opened, 2)
        self.assertEqual(summary.contact, 1)
        self.assertEqual(summary.declined, 1)
        self.assertEqual(summary.no_reaction, 1)
        self.assertEqual(
            RequestAdmin(Request, admin.site).seller_reaction_summary(obj),
            'Отправлено: 3 · открыли: 2 · к контакту: 1 · отказ: 1 · без реакции: 1',
        )

    def test_dispatch_admin_labels_reaction_from_existing_events(self):
        model_admin = RequestDispatchAdmin(RequestDispatch, admin.site)
        queryset = model_admin.get_queryset(self._admin_request())

        silent = queryset.get(pk=self.silent_dispatch.pk)
        contact = queryset.get(pk=self.contact_dispatch.pk)
        declined = queryset.get(pk=self.declined_dispatch.pk)

        self.assertEqual(model_admin.seller_reaction(silent), 'Без реакции')
        self.assertEqual(model_admin.seller_reaction(contact), 'Перешёл к контакту')
        self.assertEqual(model_admin.seller_reaction(declined), 'Не может выполнить')

    def test_admin_reaction_filter_can_show_only_silent_dispatches(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('admin:core_requestdispatch_changelist'),
            {'seller_reaction': REACTION_NO_REACTION},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Silent Seller')
        self.assertNotContains(response, 'Contact Seller')
        self.assertNotContains(response, 'Declined Seller')
        self.assertNotContains(response, 'Queued Seller')

    def test_request_changelist_shows_reaction_summary(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('admin:core_request_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Реакция продавцов')
        self.assertContains(response, 'без реакции: 1')
