from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from core.models import Request, Seller, BuyerSellerSelection, SellerChannelConnection, RequestDispatch
from core.services.buyer_selection import default_message
from core.services.manual_telegram_invitation import invitation_access


@override_settings(TWO_WAY_REQUESTS_ENABLED=True, ALLOWED_HOSTS=['testserver'])
class BuyerSelectionTests(TestCase):
    def setUp(self):
        self.req = Request.objects.create(brand='Mercedes-Benz', model='E400', city='Алматы',
            transport_type='car', description='Нужны два ШРУСа', category='Трансмиссия',
            search_scope='city', public_summary='ШРУС', contact_consent_at=timezone.now())
        self.shop = Seller.objects.create(name='Тест каталога', city='Алматы', whatsapp='77001112233',
            sells_new_parts=True, all_brands=True, all_models=True, all_categories=True,
            transport_type='car', direct_contacts_enabled=True)

    def url(self, step):
        return reverse('buyer_selection', args=[self.req.pk, self.req.access_token, step])

    def toggle(self, shop=None, **data):
        return self.client.post(reverse('buyer_selection_toggle', args=[self.req.pk,
            self.req.access_token, (shop or self.shop).pk]), data)

    def test_limit_and_persistence_across_pages(self):
        with patch('core.services.buyer_selection.eligible', return_value=[{'seller': self.shop}]):
            self.toggle()
        for number in range(9):
            shop = Seller.objects.create(name=str(number), city='Алматы')
            BuyerSellerSelection.objects.create(request=self.req, seller=shop)
        extra = Seller.objects.create(name='Extra', city='Алматы')
        self.toggle(extra)
        self.assertEqual(self.req.buyer_selections.count(), 10)
        self.assertContains(self.client.get(self.url('selected')), '10 из 10')
        self.toggle()
        self.assertEqual(self.req.buyer_selections.count(), 9)

    def test_invalid_token_expiry_and_csrf(self):
        from uuid import uuid4
        url = reverse('buyer_selection', args=[self.req.pk, uuid4(), 'selected'])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.req.access_revoked = True
        self.req.save()
        self.assertEqual(self.client.get(self.url('selected')).status_code, 410)
        from django.test import Client
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url('help')).status_code, 403)

    def test_nonmatching_or_test_seller_cannot_be_selected(self):
        with patch('core.services.buyer_selection.eligible', return_value=[]):
            self.assertEqual(self.toggle().status_code, 404)
        self.assertFalse(self.req.buyer_selections.exists())

    def test_message_invitation_has_scoped_access_and_no_buyer_token(self):
        entry = BuyerSellerSelection.objects.create(request=self.req, seller=self.shop)
        response = self.client.get(self.url('message'), {'seller': self.shop.pk})
        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.access_id)
        text = default_message(self.req, entry)
        self.assertIn('Нужны два ШРУСа', text)
        self.assertIn('Telegram ZPT.KZ', text)
        self.assertNotIn(str(self.req.access_token), text)
        self.assertIsNotNone(invitation_access(entry.access.token))
        self.assertFalse(RequestDispatch.objects.filter(request=self.req).exists())
        self.assertFalse(SellerChannelConnection.objects.filter(seller=self.shop).exists())
        SellerChannelConnection.objects.create(seller=self.shop, telegram_id=123, telegram_consent=True)
        self.assertNotIn('Подключитесь', default_message(self.req, entry))

    def test_contact_click_is_not_sent_or_consent_and_text_is_preserved(self):
        entry = BuyerSellerSelection.objects.create(request=self.req, seller=self.shop)
        with patch('core.services.buyer_selection.eligible', return_value=[{'seller': self.shop}]):
            response = self.client.post(self.url('message') + '?seller=' + str(self.shop.pk),
                {'action': 'whatsapp', 'message': 'Мой уточнённый текст'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('wa.me', response.url)
        entry.refresh_from_db()
        self.assertIsNone(entry.sent_at)
        self.assertEqual(entry.message, 'Мой уточнённый текст')
        self.assertFalse(SellerChannelConnection.objects.exists())
        self.client.post(self.url('confirm') + '?seller=' + str(self.shop.pk), {'action': 'sent'})
        self.client.post(self.url('confirm') + '?seller=' + str(self.shop.pk), {'action': 'sent'})
        entry.refresh_from_db()
        self.assertIsNotNone(entry.sent_at)
        self.assertEqual(self.req.outcome_events.filter(result='buyer_sent').count(), 1)

    def test_city_choice_does_not_change_request(self):
        from core.services.buyer_selection import catalog_request
        scoped = catalog_request(self.req, '*')
        self.assertEqual(scoped.search_scope, 'kazakhstan')
        self.req.refresh_from_db()
        self.assertEqual(self.req.search_scope, 'city')
        self.assertEqual(self.req.city, 'Алматы')

    def test_help_and_outcome_visible_in_history(self):
        entry = BuyerSellerSelection.objects.create(request=self.req, seller=self.shop)
        self.client.post(self.url('help'), {'comment': 'Помогите найти'})
        self.client.post(self.url('outcome') + '?seller=' + str(self.shop.pk),
            {'result': 'offer', 'comment': '15000 тенге'})
        self.assertContains(self.client.get(self.url('history')), 'Помогите найти')
        self.assertContains(self.client.get(self.url('history')), '15000 тенге')
        self.assertEqual(self.client.get(self.url('message'), {'seller': 99999}).status_code, 404)

    def test_real_catalog_and_cross_city_selection_recheck(self):
        from core.services.buyer_selection import eligible
        self.assertIn(self.shop.pk, [item['seller'].pk for item in eligible(self.req)])
        self.shop.city = 'Астана'
        self.shop.save()
        self.assertNotIn(self.shop.pk, [item['seller'].pk for item in eligible(self.req)])
        self.assertEqual(self.toggle(city='*').status_code, 302)
        self.assertTrue(self.req.buyer_selections.filter(seller=self.shop).exists())
        self.shop.is_test_seller = True
        self.shop.save()
        self.assertNotIn(self.shop.pk, [item['seller'].pk for item in eligible(self.req, '*')])
        self.assertEqual(self.client.post(self.url('message') + '?seller=' + str(self.shop.pk),
            {'action': 'whatsapp', 'message': 'Здравствуйте'}).status_code, 409)

    def test_preview_is_read_only_and_uses_no_real_data(self):
        for screen in ('accepted', 'edit', 'sellers', 'selected', 'message', 'confirm', 'outcome', 'history', 'help'):
            response = self.client.get(reverse('buyer_selection_preview'), {'screen': screen})
            self.assertEqual(response.status_code, 200, screen)
            self.assertContains(response, 'демонстрационные данные')
            self.assertNotContains(response, '/connect/buyer/0/')
            self.assertNotContains(response, str(self.req.access_token))
        self.assertEqual(self.client.post(reverse('buyer_selection_preview')).status_code, 405)
        self.assertFalse(BuyerSellerSelection.objects.exists())
