import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import CashEntry, DocumentAttachment, GoodsDocument, Item, MoneyAccount, StockDocument, StockEntry
from .models import Activity, Draft, Membership, Offer, PurchaseOrder, Supplier, VehicleFitment


class KaspiPurchasedWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('kaspi-owner', password='local-test')
        cls.reader = get_user_model().objects.create_user('kaspi-reader')
        Membership.objects.create(user=cls.reader, role='user')
        cls.other = get_user_model().objects.create_user('kaspi-other')
        Membership.objects.create(user=cls.other, role='sales')
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(supplier=cls.supplier, brand='WXQP', article='00123',
            name='Точная деталь', cost=9999, description='Описание из каталога',
            photo_url='https://example.com/part.jpg', category='Категория поставщика')
        cls.item = Item.objects.create(code='omega-exact', brand='WXQP', article='00123', name='Деталь из закупки')
        cls.receipt = GoodsDocument.objects.create(kind='receipt', number='00ТК-029813', counterparty='Омега',
            date=timezone.localdate(), accepted_on=timezone.localdate(), warehouse='PP2', created_by=cls.admin)
        cls.line = StockDocument.objects.create(bundle=cls.receipt, kind='receipt', status='posted', item=cls.item,
            warehouse='PP2', quantity=2, unit_cost=Decimal('5606'), date=timezone.localdate(), created_by=cls.admin)
        StockEntry.objects.create(document=cls.line, warehouse='PP2', quantity=1, created_by=cls.admin)
        DocumentAttachment.objects.create(document=cls.receipt, name='Omega_order_1.xlsx', digest='test',
            data=b'test-original', created_by=cls.admin)
        VehicleFitment.objects.create(offer=cls.offer, make='Toyota', model='Test', verified=True,
            source_url='https://example.com/catalogue')
        VehicleFitment.objects.create(offer=cls.offer, make='Unverified', model='Must not show', verified=False,
            source_url='https://example.com/unverified')
        cls.order = PurchaseOrder.objects.create(user=cls.reader, supplier=cls.supplier, status='paid',
            request_key=uuid.uuid4(), estimated_total=100, items=[dict(offer_id=cls.offer.pk, brand='WXQP',
                article='00123', name='Заказанная деталь', quantity=1, cost='100')])

    def page(self, **params):
        return self.client.get(reverse('procurement:kaspi_products'), {'batch': f'receipt:{self.receipt.pk}', **params})

    def test_receipt_uses_purchase_prices_originals_and_real_stock_without_writes(self):
        self.client.force_login(self.admin)
        before = (StockDocument.objects.count(), StockEntry.objects.count(), Draft.objects.count(), Activity.objects.count())
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_supplier'], self.supplier)
        batch = response.context['batch']
        self.assertEqual((batch['positions'], batch['quantity'], batch['total']), (1, 2, Decimal('11212')))
        row = response.context['active']
        self.assertEqual(row['cost'], Decimal('5606'))  # not the latest supplier price
        self.assertEqual(row['stock'], 1)  # not purchased quantity
        self.assertEqual([f.make for f in row['fitments']], ['Toyota'])
        self.assertEqual(before, (StockDocument.objects.count(), StockEntry.objects.count(), Draft.objects.count(), Activity.objects.count()))
        self.assertContains(self.page(tab='files'), 'Omega_order_1.xlsx')

    def test_existing_ownership_and_accounting_permission_are_preserved(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.client.get(reverse('procurement:kaspi_products'), {'batch': f'order:{self.order.pk}'}).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('procurement:kaspi_products'), {'batch': f'order:{self.order.pk}'}).status_code, 404)
        self.assertEqual(self.page().status_code, 404)
        self.client.logout()
        self.assertEqual(self.page().status_code, 302)

    def test_draft_and_cancelled_orders_are_excluded(self):
        self.client.force_login(self.reader)
        for status in ['draft', 'sent', 'cancelled']:
            self.order.status = status
            self.order.save(update_fields=['status'])
            response = self.client.get(reverse('procurement:kaspi_products'))
            self.assertEqual(response.context['batches'], [])

    def test_recorded_payment_qualifies_order_without_legacy_paid_status(self):
        self.client.force_login(self.reader)
        self.order.status = 'confirmed'
        self.order.save(update_fields=['status'])
        account = MoneyAccount.objects.create(name='Тестовый счёт', opening_date=timezone.localdate(), created_by=self.reader)
        CashEntry.objects.create(account=account, purchase_order=self.order, kind='supplier', amount=100,
            date=timezone.localdate(), reference='paid-1', created_by=self.reader)
        response = self.client.get(reverse('procurement:kaspi_products'))
        self.assertEqual([b['key'] for b in response.context['batches']], [f'order:{self.order.pk}'])
        CashEntry.objects.create(account=account, purchase_order=self.order, kind='supplier_refund', amount=100,
            date=timezone.localdate(), reference='refund-1', created_by=self.reader)
        self.assertEqual(self.client.get(reverse('procurement:kaspi_products')).context['batches'], [])

    def test_prepare_is_repeatable_and_preserves_manual_card_and_stock(self):
        self.client.force_login(self.admin)
        response = self.page()
        payload = {'action': 'prepare', 'selected': ['0'], 'source_token': response.context['batch']['source_token']}
        url = response.request['PATH_INFO'] + f'?batch=receipt:{self.receipt.pk}'
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        card = Draft.objects.get(offer=self.offer)
        card.description = 'Ручное описание'
        card.save()
        self.client.post(url, payload)
        self.assertEqual(Draft.objects.count(), 1)
        card.refresh_from_db()
        self.assertEqual(card.description, 'Ручное описание')
        self.assertEqual(StockDocument.objects.count(), 1)
        self.assertEqual(StockEntry.objects.count(), 1)
        self.assertEqual(Activity.objects.filter(action='kaspi_batch_prepared').count(), 1)

    def test_stale_source_and_foreign_line_are_rejected(self):
        self.client.force_login(self.admin)
        response = self.page()
        token = response.context['batch']['source_token']
        url = reverse('procurement:kaspi_products') + f'?batch=receipt:{self.receipt.pk}'
        self.assertEqual(self.client.post(url, {'action': 'prepare', 'selected': ['99'], 'source_token': token}).status_code, 400)
        self.line.quantity = 3
        self.line.save(update_fields=['quantity'])
        self.assertEqual(self.client.post(url, {'action': 'prepare', 'selected': ['0'], 'source_token': token}).status_code, 409)
        self.assertFalse(Draft.objects.exists())

    def test_similar_article_does_not_supply_card_materials(self):
        self.client.force_login(self.admin)
        self.item.article = '00123-A'
        self.item.save()
        response = self.page()
        self.assertTrue(response.context['active']['link_issue'])
        self.assertEqual(response.context['active']['photo'], '')
        self.assertIsNone(response.context['card_form'])

    def test_editor_saves_history_and_detects_concurrent_changes(self):
        self.client.force_login(self.admin)
        response = self.page()
        url = reverse('procurement:kaspi_products') + f'?batch=receipt:{self.receipt.pk}'
        payload = {'action': 'save', 'source_token': response.context['batch']['source_token'],
            'draft_stamp': '', 'title': 'Проверенное название', 'description': 'Описание',
            'photo_url': 'https://example.com/exact.jpg', 'category': '', 'characteristics': '{}'}
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Activity.objects.get(action='kaspi_batch_saved').details['after']['title'], 'Проверенное название')
        self.assertEqual(self.client.post(url, payload).status_code, 400)
        history = self.page(tab='history')
        self.assertContains(history, 'Проверенное название')
        self.assertEqual(StockDocument.objects.count(), 1)

    def test_reader_cannot_prepare_cards(self):
        self.client.force_login(self.reader)
        url = reverse('procurement:kaspi_products') + f'?batch=order:{self.order.pk}'
        self.assertEqual(self.client.post(url, {'action': 'prepare'}).status_code, 403)
        self.assertFalse(Draft.objects.exists())
