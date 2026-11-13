import io
import json
import uuid
import zipfile
from decimal import Decimal
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from inventory.models import DocumentAttachment, GoodsDocument, Item, StockDocument, StockEntry
from .models import (Membership, Supplier, Offer, PurchaseOrder, VehicleFitment, Activity,
    KaspiPreparedCard, KaspiCardImage, KaspiCardPrice, KaspiPricingRule, Preferences, KaspiSellerSnapshot)
from .kaspi_materials import process_purchased_cards, encode_image, prepare_batch, pricing, competitor_data


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
            name='Точная деталь', cost=9999, description='Описание детали\nЦена: 9999\nОстаток: 12',
            photo_url='https://example.com/part.jpg', category='Категория поставщика')
        cls.item = Item.objects.create(code='omega-exact', brand='WXQP', article='00123', name='Деталь из закупки')
        cls.receipt = GoodsDocument.objects.create(kind='receipt', number='00ТК-029813', counterparty='Омега',
            date=timezone.localdate(), warehouse='PP2', created_by=cls.admin)
        cls.line = StockDocument.objects.create(bundle=cls.receipt, kind='receipt', status='posted', item=cls.item,
            warehouse='PP2', quantity=2, unit_cost=Decimal('5606'), date=timezone.localdate(), created_by=cls.admin)
        StockEntry.objects.create(document=cls.line, warehouse='PP2', quantity=1, created_by=cls.admin)
        DocumentAttachment.objects.create(document=cls.receipt, name='Omega_order_1.xlsx', digest='test',
            data=b'original', created_by=cls.admin)
        VehicleFitment.objects.create(offer=cls.offer, make='Toyota', model='Test', verified=True,
            source_url='https://example.com/catalogue', configurations=[{'years': '2000-2005', 'engine': 'Test engine'}])
        VehicleFitment.objects.create(offer=cls.offer, make='Unverified', model='Must not show', verified=False,
            source_url='https://example.com/unverified')
        cls.order = PurchaseOrder.objects.create(user=cls.reader, supplier=cls.supplier, status='paid',
            request_key=uuid.uuid4(), estimated_total=100, items=[dict(brand='WXQP',
                article='00123', name='Заказанная деталь', quantity=1, cost='100')])

    def setUp(self):
        self.client.force_login(self.admin)

    def page(self, **params):
        return self.client.get(reverse('procurement:kaspi_products'), {'batch': f'receipt:{self.receipt.pk}', **params})

    def prepare(self):
        process_purchased_cards(fetch_photos=False)
        return KaspiPreparedCard.objects.get(brand='WXQP', article='00123')

    def test_list_uses_original_prices_and_one_click_card_without_get_writes(self):
        before = (StockDocument.objects.count(), StockEntry.objects.count(), KaspiPreparedCard.objects.count())
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['batch']['total'], Decimal('11212'))
        self.assertEqual(response.context['rows'][0]['cost'], Decimal('5606'))
        self.assertNotContains(response, 'Подготовить выбранные')
        self.assertContains(response, 'Закупочный лист')
        self.assertContains(response, 'line=0')
        self.assertEqual(before, (StockDocument.objects.count(), StockEntry.objects.count(), KaspiPreparedCard.objects.count()))
        detail = self.page(line=0)
        self.assertContains(detail, 'Фотографии для Kaspi')
        self.assertNotContains(detail, 'kc-list-row')
        self.assertEqual(detail.context['active']['stock'], 1)
        self.assertEqual(detail.context['selected_supplier'], self.supplier)

    def test_receipt_and_other_order_access_remain_protected(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.client.get(reverse('procurement:kaspi_products'), {'batch': f'order:{self.order.pk}'}).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.page().status_code, 404)
        self.assertEqual(self.client.get(reverse('procurement:kaspi_products'), {'batch': f'order:{self.order.pk}'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.page().status_code, 302)

    def test_automatic_preparation_is_idempotent_and_cleans_internal_description(self):
        card = self.prepare()
        self.assertNotIn('Цена', card.description)
        self.assertNotIn('Остаток', card.description)
        self.assertIn('2000-2005', card.applicability)
        self.assertNotIn('Unverified', card.applicability)
        count = KaspiPreparedCard.objects.count()
        self.prepare()
        self.assertEqual(KaspiPreparedCard.objects.count(), count)
        self.assertEqual(StockDocument.objects.count(), 1)
        self.assertEqual(StockEntry.objects.count(), 1)

    def test_manual_fields_survive_source_refresh(self):
        card = self.prepare()
        response = self.page(line=0)
        payload = {'action': 'save_material', 'source_token': response.context['source_token'],
            'revision': card.revision, 'title': 'Ручное название', 'description': 'Ручное описание',
            'category': '', 'attributes': '', 'applicability': '', 'applicability_source': ''}
        url = reverse('procurement:kaspi_products') + f'?batch=receipt:{self.receipt.pk}&line=0'
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(self.client.post(url, payload).status_code, 400)
        self.offer.description = 'Новый источник'
        self.offer.save(update_fields=['description'])
        self.prepare()
        card.refresh_from_db()
        self.assertEqual(card.description, 'Ручное описание')
        self.assertEqual(card.title, 'Ручное название')
        self.assertTrue(Activity.objects.filter(action='kaspi_card_saved').exists())

    def test_upload_thumbnail_replace_and_scoped_download(self):
        card = self.prepare()
        data = io.BytesIO()
        Image.new('RGB', (600, 600), 'white').save(data, 'PNG')
        response = self.page(line=0)
        url = reverse('procurement:kaspi_products') + f'?batch=receipt:{self.receipt.pk}&line=0'
        payload = {'action': 'upload_photo', 'source_token': response.context['source_token'],
            'revision': card.revision, 'photo': SimpleUploadedFile('part.png', data.getvalue(), content_type='image/png')}
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        asset = KaspiCardImage.objects.get(card=card)
        self.assertEqual((asset.width, asset.height), (600, 600))
        self.assertLess(len(asset.thumbnail), len(asset.data))
        downloaded = self.page(line=0, image=asset.pk, thumb='1')
        self.assertEqual(downloaded['Content-Type'], 'image/jpeg')
        self.client.force_login(self.other)
        self.assertEqual(self.page(line=0, image=asset.pk).status_code, 404)

    def test_small_or_invalid_photo_is_rejected(self):
        self.prepare()
        data = io.BytesIO()
        Image.new('RGB', (100, 100)).save(data, 'PNG')
        with self.assertRaises(ValueError):
            encode_image(data.getvalue(), require_size=True)
        with self.assertRaises(ValueError):
            encode_image(b'not an image', require_size=True)

    def test_price_modes_use_purchase_cost_and_do_not_overwrite_manual_price(self):
        card = self.prepare()
        Preferences.objects.create(user=self.admin, commission=Decimal('10'), expenses=Decimal('250'))
        KaspiPricingRule.objects.create(user=self.admin, enabled=True, method='cost_plus_percent', value=100, min_profit=1000)
        row = {'cost': Decimal('5606')}
        result = pricing(self.admin, card, row, {'usable': False})
        self.assertEqual(result['recommended'], Decimal('11212'))
        KaspiCardPrice.objects.create(card=card, user=self.admin, mode='manual', amount=8000)
        self.assertEqual(pricing(self.admin, card, row, {'usable': False})['amount'], Decimal('8000'))
        self.prepare()
        self.assertEqual(KaspiCardPrice.objects.get(card=card, user=self.admin).amount, Decimal('8000'))

    def test_missing_or_unverified_competitors_do_not_drive_recommendation(self):
        card = self.prepare()
        Preferences.objects.create(user=self.admin, commission=10, expenses=250)
        KaspiPricingRule.objects.create(user=self.admin, enabled=True, method='minimum_minus_amount', value=100)
        self.offer.kaspi_url = 'https://kaspi.kz/shop/p/test-123/'
        self.offer.save(update_fields=['kaspi_url'])
        KaspiSellerSnapshot.objects.create(offer=self.offer, source_url=self.offer.kaspi_url,
            checked_on=timezone.localdate(), complete=True, sellers=[{'name': 'Shop', 'price': '10000', 'url': 'https://kaspi.kz/shop/m/test/'}])
        competitors = competitor_data(self.offer)
        self.assertFalse(competitors['usable'])
        self.assertIsNone(pricing(self.admin, card, {'cost': 5606}, competitors)['recommended'])

    def test_zip_contains_customer_material_and_local_images_not_purchase_notes(self):
        card = self.prepare()
        response = self.page(line=0, download='zip')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            material = json.loads(archive.read('card.json'))
            self.assertNotIn('cost', material)
            self.assertNotIn('Цена: 9999', material['description'])
        self.assertContains(self.page(tab='files'), 'Omega_order_1.xlsx')

    def test_unknown_article_does_not_borrow_materials(self):
        self.item.article = '00123-A'
        self.item.save()
        self.prepare_other()
        response = self.page(line=0)
        self.assertEqual(response.context['card'].photo_url, '')
        self.assertEqual(response.context['card'].applicability, '')

    def prepare_other(self):
        process_purchased_cards(fetch_photos=False)

    def test_reader_cannot_edit_or_upload(self):
        self.client.force_login(self.reader)
        url = reverse('procurement:kaspi_products') + f'?batch=order:{self.order.pk}&line=0'
        self.assertEqual(self.client.post(url, {'action': 'save_price', 'mode': 'manual', 'amount': 1}).status_code, 403)

    def test_stale_purchase_and_foreign_photo_are_rejected(self):
        card = self.prepare()
        page = self.page(line=0)
        self.line.quantity = 3
        self.line.save(update_fields=['quantity'])
        url = reverse('procurement:kaspi_products') + f'?batch=receipt:{self.receipt.pk}&line=0'
        self.assertEqual(self.client.post(url, {'action': 'save_material',
            'source_token': page.context['source_token']}).status_code, 409)
        self.assertEqual(self.page(line=0, image='9999').status_code, 404)
