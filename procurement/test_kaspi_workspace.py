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
        bundle = self.page(line=0, download='zip')
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            self.assertEqual(archive.read('images/photo-1.jpg'), bytes(asset.data))
        card.refresh_from_db()
        replacement = io.BytesIO()
        Image.new('RGB', (600, 600), 'blue').save(replacement, 'PNG')
        response = self.page(line=0)
        self.assertEqual(self.client.post(url, {'action': 'upload_photo',
            'source_token': response.context['source_token'], 'revision': card.revision,
            'replace_image': asset.pk, 'photo': SimpleUploadedFile('new.png', replacement.getvalue())}).status_code, 302)
        asset.refresh_from_db()
        self.assertFalse(asset.active)
        self.assertEqual(KaspiCardImage.objects.filter(card=card, active=True).count(), 1)
        self.assertEqual(KaspiCardImage.objects.filter(card=card).count(), 2)
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

    def test_images_cache_privately_but_html_and_denials_do_not(self):
        from .kaspi_materials import store_image
        card = self.prepare()
        data = io.BytesIO()
        Image.new('RGB', (600, 600), 'white').save(data, 'PNG')
        asset = store_image(card, encode_image(data.getvalue()))
        response = self.page(line=0, image=asset.pk, thumb='1')
        self.assertEqual(response['Cache-Control'], 'private, max-age=86400, immutable')
        self.assertNotIn('Pragma', response)
        self.assertIn('Cookie', response['Vary'])
        self.assertIn('no-store', self.page()['Cache-Control'])
        self.client.force_login(self.other)
        self.assertEqual(self.page(line=0, image=asset.pk).status_code, 404)
        self.client.logout()
        self.assertIn('no-store', self.page(line=0, image=asset.pk)['Cache-Control'])

    def test_list_names_missing_materials_and_readiness_uses_our_price(self):
        from .kaspi_materials import store_image
        card = self.prepare()
        response = self.page()
        self.assertNotContains(response, 'Требует проверки')
        self.assertContains(response, 'Нет фото от 500 × 500')
        self.assertContains(response, 'Цена:')
        self.assertNotContains(response, 'Ожидает проверки менеджером')
        data = io.BytesIO()
        Image.new('RGB', (600, 600), 'white').save(data, 'PNG')
        store_image(card, encode_image(data.getvalue()))
        card.category = 'Master - Test'
        card.characteristics = {'Материал': 'Сталь'}
        card.save()
        KaspiCardPrice.objects.create(user=self.admin, card=card, mode='manual', amount=9000)
        self.assertEqual(self.page().context['rows'][0]['status'], 'Нужно дополнить материалы')
        self.assertContains(self.page(), 'Не получены требования выбранной категории Kaspi.')
        card.verified = True
        card.save()
        self.assertEqual(self.page().context['rows'][0]['status'], 'Материалы проверены')

    def test_pricing_accepts_comma_percent_and_explains_floor(self):
        from .kaspi_card_forms import PricingRuleForm
        form = PricingRuleForm(data={'enabled': True, 'method': 'minimum_minus_percent', 'value': '2,5', 'min_profit': 1000})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['value'], Decimal('2.5'))
        self.assertEqual(form.fields['value'].label, 'Процент снижения, %')
        invalid = PricingRuleForm(data={'method': 'minimum_minus_percent', 'value': 100, 'min_profit': 1000})
        self.assertFalse(invalid.is_valid())
        card = self.prepare()
        Preferences.objects.create(user=self.admin, commission=10, expenses=250)
        rule = KaspiPricingRule.objects.create(user=self.admin, enabled=True, method='minimum_minus_percent', value=3, min_profit=1000)
        result = pricing(self.admin, card, {'cost': 5606}, {'usable': True, 'sellers': [{'price': 10000}]})
        self.assertEqual(result['recommended'], Decimal('9700'))
        rule.value = 50
        rule.save()
        result = pricing(self.admin, card, {'cost': 5606}, {'usable': True, 'sellers': [{'price': 10000}]})
        self.assertEqual(result['recommended'], Decimal('7618'))
        self.assertIn('повышена до границы', result['explanation'])
        self.assertContains(self.client.get(reverse('procurement:kaspi_card_pricing')), 'data-office-back')

    def test_image_request_skips_card_scope_and_enrichment(self):
        from unittest.mock import patch
        from .kaspi_materials import store_image
        card = self.prepare()
        data = io.BytesIO()
        Image.new('RGB', (600, 600), 'white').save(data, 'PNG')
        asset = store_image(card, encode_image(data.getvalue()))
        with patch('procurement.kaspi_card_views.scope', side_effect=AssertionError('Heavy card scope')):
            response = self.page(line=0, image=asset.pk, thumb='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, bytes(asset.thumbnail))
        self.assertEqual(self.page(line=0, image=asset.pk, thumb='1', batch='receipt:9999').status_code, 404)

    def test_default_three_percent_requires_no_get_writes(self):
        card=self.prepare()
        Preferences.objects.create(user=self.admin,commission=10,expenses=250)
        result=pricing(self.admin,card,{'cost':5606},{'usable':True,'sellers':[{'price':'10000'}]})
        self.assertEqual(result['recommended'],Decimal('9700'))
        self.assertEqual(KaspiPricingRule.objects.count(),0)
        response=self.page(line=0)
        for text in ('Копировать название','Копировать описание','Копировать характеристики','Опубликовать на Kaspi','Обновить'):
            self.assertContains(response,text)

    def test_refresh_runs_in_background_and_preserves_manual_price(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from .models import KaspiCardJob
        from .kaspi_card_jobs import process_card_jobs
        cache.delete('kaspi-card-refresh-paused')
        card=self.prepare()
        self.offer.kaspi_url='https://kaspi.kz/shop/p/test-123/'
        self.offer.save(update_fields=['kaspi_url'])
        KaspiCardPrice.objects.create(card=card,user=self.admin,mode='manual',amount=9000)
        page=self.page(line=0)
        url=reverse('procurement:kaspi_products')+f'?batch=receipt:{self.receipt.pk}&line=0'
        data={'action':'refresh_competitors','source_token':page.context['source_token']}
        with patch('procurement.kaspi_refresh.read_complete_sellers') as reader:
            self.assertEqual(self.client.post(url,data).status_code,302)
            self.assertEqual(self.client.post(url,data).status_code,302)
            reader.assert_not_called()
        self.assertEqual(KaspiCardJob.objects.count(),1)
        sellers=[{'merchant_id':'1','name':'Shop','price':'10000','url':'https://kaspi.kz/shop/m/1/'}]
        with patch('procurement.kaspi_refresh.read_complete_sellers',return_value=sellers):
            process_card_jobs()
        self.assertEqual(KaspiCardJob.objects.get().status,'done')
        self.assertEqual(KaspiSellerSnapshot.objects.get(offer=self.offer).sellers,sellers)
        self.assertEqual(KaspiCardPrice.objects.get(card=card).amount,Decimal('9000'))
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.match_verified)

    def test_failed_refresh_keeps_old_sellers(self):
        from unittest.mock import patch
        from django.core.cache import cache
        from .kaspi_card_jobs import queue_job,process_card_jobs
        cache.delete('kaspi-card-refresh-paused')
        card=self.prepare()
        self.offer.kaspi_url='https://kaspi.kz/shop/p/test-123/'
        self.offer.save(update_fields=['kaspi_url'])
        old=KaspiSellerSnapshot.objects.create(offer=self.offer,source_url=self.offer.kaspi_url,
            checked_on=timezone.localdate(),complete=True,sellers=[{'name':'Old','price':'5000'}])
        job,_=queue_job(card,self.admin,'refresh',dict(batch=f'receipt:{self.receipt.pk}',line=0))
        with patch('procurement.kaspi_refresh.read_complete_sellers',side_effect=ValueError('HTTP 403')):
            process_card_jobs()
        job.refresh_from_db();old.refresh_from_db()
        self.assertEqual(job.status,'error')
        self.assertEqual(old.sellers,[{'name':'Old','price':'5000'}])
        cache.delete('kaspi-card-refresh-paused')

    def publish_fixture(self):
        from .kaspi_materials import store_image
        card=self.prepare()
        card.category='Master - Test'
        card.characteristics={'Материал':'сталь'}
        card.source['kaspi_schema']={'category':card.category,'attributes':[{'code':'material','title':'Материал','type':'string','mandatory':True}]}
        card.save()
        data=io.BytesIO();Image.new('RGB',(600,600),'white').save(data,'PNG')
        store_image(card,encode_image(data.getvalue()))
        Preferences.objects.create(user=self.admin,commission=10,expenses=250)
        KaspiCardPrice.objects.create(card=card,user=self.admin,mode='manual',amount=9000)
        return card

    def test_publish_is_explicit_idempotent_and_acceptance_is_not_sale(self):
        from unittest.mock import patch
        from django.test import override_settings
        from datetime import timedelta
        from .models import KaspiCardJob
        from .kaspi_card_jobs import process_card_jobs
        with override_settings(KASPI_API_TOKEN='unit-test-token',KASPI_MERCHANT_ID='1',KASPI_POINT_OF_SERVICE='PP2',KASPI_BASELINE_PRICE_FEED_URL='https://example.com/base.xml',KASPI_PRICE_FEED_CONNECTED=True):
            card=self.publish_fixture()
            page=self.page(line=0)
            self.assertTrue(page.context['ready'])
            url=reverse('procurement:kaspi_products')+f'?batch=receipt:{self.receipt.pk}&line=0'
            data={'action':'publish_kaspi','source_token':page.context['source_token']}
            with patch('procurement.kaspi_catalog_api.CatalogClient.submit',return_value={'code':'import-1','status':'UPLOADED'}) as submit:
                self.client.post(url,data);self.client.post(url,data)
                submit.assert_not_called()
                self.assertEqual(KaspiCardJob.objects.count(),1)
                process_card_jobs()
                self.assertEqual(submit.call_count,1)
                self.client.post(url,data)
                process_card_jobs()
                self.assertEqual(submit.call_count,1)
            job=KaspiCardJob.objects.get();self.assertEqual(job.status,'submitted')
            KaspiCardJob.objects.filter(pk=job.pk).update(updated_at=timezone.now()-timedelta(seconds=31))
            with patch('procurement.kaspi_catalog_api.CatalogClient.status',return_value={'status':'FINISHED'}),patch('procurement.kaspi_catalog_api.CatalogClient.result',return_value={'errors':0,'total':1,'result':{'ZPT-'+card.identity[:16]:{'state':'FINISHED'}}}):
                process_card_jobs()
            job.refresh_from_db()
            self.assertEqual(job.status,'awaiting_listing')
            self.assertIn('размещение ещё не подтверждено',job.reason)

    def test_uncertain_publish_is_never_repeated(self):
        from unittest.mock import patch
        from django.test import override_settings
        from .kaspi_catalog_api import CatalogUncertain
        from .kaspi_card_jobs import queue_job,process_card_jobs,freeze_publication
        with override_settings(KASPI_API_TOKEN='unit-test-token',KASPI_MERCHANT_ID='1',KASPI_POINT_OF_SERVICE='PP2',KASPI_BASELINE_PRICE_FEED_URL='https://example.com/base.xml',KASPI_PRICE_FEED_CONNECTED=True):
            card=self.publish_fixture();page=self.page(line=0)
            payload=freeze_publication(card,self.admin,page.context['active'],page.context['batch'],page.context['source_token'])
            job,_=queue_job(card,self.admin,'publish',payload)
            with patch('procurement.kaspi_catalog_api.CatalogClient.submit',side_effect=CatalogUncertain('Unknown')) as submit:
                process_card_jobs();process_card_jobs()
                job.refresh_from_db();self.assertEqual(job.status,'unknown')
                _,created=queue_job(card,self.admin,'publish',payload)
                self.assertFalse(created)
                self.assertEqual(submit.call_count,1)

    def test_public_photo_requires_valid_limited_token(self):
        from .kaspi_catalog_api import signed_image_url
        card=self.publish_fixture()
        asset=card.images.get()
        url=signed_image_url(asset)
        from urllib.parse import urlsplit
        self.client.logout()
        self.assertEqual(self.client.get(urlsplit(url).path).status_code,200)
        self.assertEqual(self.client.get(urlsplit(url).path+'bad').status_code,404)

    def test_automatic_sources_never_accept_similar_article_or_erase_manual_text(self):
        from unittest.mock import patch
        from .kaspi_enrichment import source_material,enrich_card
        card=self.prepare()
        raw={'@type':'Product','sku':'00123-A','brand':{'name':'WXQP'},'image':['https://example.com/photo.jpg']}
        page='<script type="application/ld+json">'+json.dumps(raw)+'</script>'
        self.assertEqual(source_material(page,'https://example.com',card)['images'],[])
        raw['sku']='00123';raw['description']='Описание из источника';raw['additionalProperty']=[{'name':'Материал','value':'сталь'}];raw['image']=[]
        page='<script type="application/ld+json">'+json.dumps(raw)+'</script>'
        card.description='Ручное описание';card.manual_fields=['description'];card.save()
        self.offer.supplier_url='https://example.com/exact'
        row=dict(index=0,name=self.offer.name,brand=self.offer.brand,article=self.offer.article,offer=self.offer)
        with patch('procurement.kaspi_enrichment.fetch_page',return_value=page),patch('procurement.kaspi_enrichment.connected',return_value=False):
            enrich_card(row,card,self.admin,f'receipt:{self.receipt.pk}')
        card.refresh_from_db();self.assertEqual(card.description,'Ручное описание')
        self.assertEqual(card.characteristics['Материал'],'сталь')
        self.prepare();card.refresh_from_db()
        self.assertEqual(card.characteristics['Материал'],'сталь')

    def test_full_price_feed_preserves_other_products_and_escapes_text(self):
        from .kaspi_price_feed import merge_feed
        from xml.etree import ElementTree as ET
        original='<kaspi_catalog xmlns="kaspiShopping"><merchantid>shop</merchantid><offers><offer sku="old"><model>Old</model><price>123</price></offer></offers></kaspi_catalog>'
        row=dict(sku='ZPT-001',title='Деталь & крепёж',brand='Brand',quantity=2,price='9700')
        text=merge_feed(original,[row,row],merchant='shop',store='PP2')
        root=ET.fromstring(text)
        offers=root.findall('{kaspiShopping}offers/{kaspiShopping}offer')
        self.assertEqual(len(offers),2)
        self.assertEqual(offers[0].get('sku'),'old')
        self.assertEqual(offers[0].findtext('{kaspiShopping}price'),'123')
        self.assertEqual(offers[1].findtext('{kaspiShopping}model'),'Деталь & крепёж')
        with self.assertRaises(ValueError): merge_feed(original,[row],merchant='wrong',store='PP2')
        with self.assertRaises(ValueError): merge_feed(original,[{**row,'quantity':None}],merchant='shop',store='PP2')

    def test_job_status_is_read_only_and_publication_is_shared_between_managers(self):
        from .kaspi_card_jobs import queue_job
        from .models import KaspiCardJob
        card=self.prepare()
        job,created=queue_job(card,self.admin,'publish',dict(batch=f'receipt:{self.receipt.pk}',line=0))
        other,created=queue_job(card,self.other,'publish',dict(batch=f'receipt:{self.receipt.pk}',line=0))
        self.assertFalse(created);self.assertEqual(other.pk,job.pk)
        response=self.page(line=0,job_status='1')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['jobs'][0]['status'],'pending')
        self.assertEqual(KaspiCardJob.objects.count(),1)

    def test_supplier_photo_gets_white_canvas_without_enlarging_part(self):
        from .kaspi_enrichment import encode_source_image
        raw=io.BytesIO();Image.new('RGB',(500,450),'red').save(raw,'PNG')
        data,thumb,width,height=encode_source_image(raw.getvalue())
        self.assertEqual((width,height),(500,500))
        with Image.open(io.BytesIO(data)) as photo:
            self.assertGreater(photo.getpixel((250,0))[1],240)
            self.assertLess(photo.getpixel((250,25))[1],30)
            self.assertLess(photo.getpixel((250,474))[1],30)
        raw=io.BytesIO();Image.new('RGB',(100,100),'red').save(raw,'PNG')
        with self.assertRaises(ValueError): encode_source_image(raw.getvalue())

    def test_auto_schema_checks_do_not_require_manager_checkbox(self):
        from .kaspi_materials import readiness,store_image
        card=self.prepare();card.category='Master - Test';card.characteristics={'Материал':'Сталь'}
        card.source['kaspi_schema']={'category':card.category,'attributes':[{'code':'material','title':'Материал','mandatory':True,'type':'string'}]}
        card.save()
        raw=io.BytesIO();Image.new('RGB',(600,600),'white').save(raw,'PNG')
        store_image(card,encode_image(raw.getvalue()))
        checks,ready=readiness(card,list(card.images.all()),{'amount':9000})
        self.assertFalse(card.verified)
        self.assertTrue(ready)

    def test_connecting_api_retries_cards_without_waiting_a_day(self):
        from unittest.mock import patch
        from procurement.kaspi_enrichment import due,ENRICHMENT_VERSION
        card=self.prepare()
        card.source['enrichment']={'version':ENRICHMENT_VERSION,'at':timezone.now().isoformat(),'reasons':['Автовыбор категории и обязательных характеристик требует API-ключа магазина Kaspi.']}
        with patch('procurement.kaspi_enrichment.connected',return_value=False): self.assertFalse(due(card))
        with patch('procurement.kaspi_enrichment.connected',return_value=True): self.assertTrue(due(card))
        card.source['enrichment']['reasons']=['API Kaspi: HTTP 403. Проверьте подключение и права ключа.']
        with patch('procurement.kaspi_enrichment.connected',return_value=True): self.assertFalse(due(card))
