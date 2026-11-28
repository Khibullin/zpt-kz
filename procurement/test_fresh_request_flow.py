"""New price -> category request -> actor INPUT -> fresh, isolated profitability."""
import hashlib
import json
from datetime import date
from urllib.parse import parse_qs, urlsplit
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .apify_batch_intake import stage_apify_file, process_next_apify_batch, begin_chunked_upload
from .apify_price_recheck import import_apify_recheck_results
from .apify_profitable_publish import publish_profitable
from .models import (Activity, Membership, Supplier, Offer, Preferences, PriceUpdate,
                     WholesaleSelectionRule, KaspiPriceAudit, PermanentBuyingItem)
from .omega_selection_requests import save_request, request_queries, preview_request
from .request_snapshot_import import audit_source


class FreshRequestFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('fresh_phaeton_manager')
        Membership.objects.create(user=self.user, role='admin')
        self.client.force_login(self.user)
        Preferences.objects.create(user=self.user, commission=Decimal('12.5'), expenses=250)
        self.phaeton = Supplier.objects.create(name='Phaeton DC')
        self.omega = Supplier.objects.create(name='Omega')
        WholesaleSelectionRule.objects.create(singleton_key=1, supplier=self.phaeton)
        self.existing = Offer.objects.create(supplier=self.phaeton, brand='MASUMA', article='ML-9108R',
            name='Старая строка каталога', category='Стойка', cost=3000, stock='0', supplier_checked_on=date(2026, 11, 1))
        self.other_supplier = Offer.objects.create(supplier=self.omega, brand='MASUMA', article='ML-9108R',
            name='Другой поставщик', cost=4000, stock='4')
        self.outside = Offer.objects.create(supplier=self.phaeton, brand='MASUMA', article='OUTSIDE',
            name='Другой запрос', cost=2000, stock='4')
        self.price = PriceUpdate.objects.create(supplier=self.phaeton, user=self.user, filename='latest.xlsx',
            checked_on=date(2026, 11, 28), sha256='fresh-price', status='done', row_count=2, rows=[
                dict(brand='MASUMA', article='ML-9108R', name='MASUMA стойка стабилизатора ML9108R',
                     category='Стойка', cost='6000', stock='5'),
                dict(brand='MASUMA', article='NEW-101', name='MASUMA стойка NEW-101',
                     category='Стойка', cost='3000', stock='2')])
        self.saved = save_request(batch_id=self.price.pk, categories=['Стойка'], minimum='2000', user=self.user)
        PermanentBuyingItem.objects.create(offer=self.existing, source='apify', title='Старая запись',
            product_id='111111111', cost_snapshot=3000, kaspi_price_snapshot=25000,
            commission_snapshot=Decimal('12.5'), expenses_snapshot=250, profit_snapshot=18625, roi_snapshot=620)
        self.old = KaspiPriceAudit.objects.create(offer=self.existing, source_filename='fresh.json',
            min_price=2000, product_id='111111111', card_url='https://kaspi.kz/shop/p/old-111111111/',
            cost_snapshot=3000, commission_snapshot=Decimal('12.5'), expenses_snapshot=250, profit=-1500, roi=-50)

    def card(self, product_id, article, price, brand='MASUMA'):
        return dict(id=product_id, brand=brand, title=brand+' стойка '+article, city='Almaty',
            url='https://kaspi.kz/shop/p/part-'+product_id+'/',
            offers=[dict(price=price, sellerName='Shop')], searchQuery=article)

    def test_new_input_uses_clean_name_complete_article_price_order_and_known_urls(self):
        data = json.loads(self.client.get(reverse('procurement:selection_apify_json'),
            {'request_id': self.saved.pk, 'city': ['Almaty', 'Astana']}).content)
        self.assertEqual(data['searchQueries'], [])
        self.assertEqual([parse_qs(urlsplit(url).query)['text'][0] for url in data['startUrls'] if 'text=' in url], ['стойка стабилизатора ML-9108R', 'стойка NEW-101'])
        self.assertEqual(data['sortOrder'], 'relevance')
        self.assertEqual(data['maxItemsPerQuery'], 20)
        self.assertEqual(data['cities'], ['Almaty', 'Astana'])
        self.assertTrue(data['includeOffers']); self.assertFalse(data['includeReviews'])
        self.assertEqual(data['maxOffersPerProduct'], 0)
        self.assertIn(self.old.card_url, data['startUrls'])
        self.assertEqual(self.client.session['apify_prepared_request_id'], self.saved.pk)
        preview = preview_request(batch_id=self.price.pk, categories=['Стойка'], minimum='2000', max_vehicle_age=20)
        self.assertEqual(preview['queries'], len(request_queries(self.saved)))
        self.saved.details['search_policy'] = 'name_article_v4'; self.saved.save()
        legacy = self.client.get(reverse('procurement:selection_apify_json'), {'request_id':self.saved.pk})
        self.assertEqual(legacy.status_code,400)

    def test_whole_fresh_file_uses_its_price_only_and_only_selected_supplier_and_skus(self):
        rows = [self.card('222222222', 'ML-9108R', 18000)] * 500 + [
            self.card('333333333', 'ML 9108R', 10000),
            self.card('444444444', 'ML9108L', 1),
            self.card('555555555', 'ML9108R', 1, brand='OTHER'),
            self.card('666666666', 'NEW101', 9000),
            self.card('777777777', 'OUTSIDE', 9000)]
        session = stage_apify_file(SimpleUploadedFile('fresh.json', json.dumps(rows).encode()), self.user, request_id=self.saved.pk)
        self.assertEqual(session.details['price_sha256'], self.price.sha256)
        self.assertNotEqual(session.details['audit_source'], 'fresh.json')
        self.assertFalse(process_next_apify_batch(session.pk, self.user)['done'])
        self.assertTrue(process_next_apify_batch(session.pk, self.user)['done'])
        source = session.details['audit_source']
        audit = KaspiPriceAudit.objects.get(offer=self.existing, source_filename=source)
        self.assertEqual(audit.cost_snapshot, Decimal('6000'))
        self.assertEqual(audit.min_price, Decimal('10000'))
        self.assertEqual(audit.profit, Decimal('2500'))
        self.assertLess(audit.roi, 50)
        self.assertEqual(audit.price_evidence['supplier_checked_on'], '2026-11-28')
        self.existing.refresh_from_db(); self.old.refresh_from_db()
        self.assertEqual(self.existing.cost, Decimal('3000'))
        self.assertEqual(self.existing.stock, '0')
        self.assertEqual(self.old.min_price, Decimal('2000'))
        self.assertFalse(KaspiPriceAudit.objects.filter(offer=self.other_supplier).exists())
        self.assertFalse(KaspiPriceAudit.objects.filter(offer=self.outside).exists())
        self.assertEqual(publish_profitable(session.pk, self.user)['added'], 1)
        self.assertEqual(publish_profitable(session.pk, self.user)['added'], 0)
        listing = self.client.get(reverse('procurement:dashboard'), {'mode':'best', 'supplier': self.phaeton.pk, 'apify_source':source})
        self.assertContains(listing, 'NEW-101')
        self.assertNotContains(listing, 'comparison/'+str(self.existing.pk)+'/')
        page = self.client.get(reverse('procurement:comparison', args=[self.existing.pk]), {'apify_source':source})
        self.assertEqual(page.context['kaspi_price'], Decimal('10000'))
        self.assertContains(page, '28.11.2026')
        self.assertContains(page, '6\u00a0000')

    def test_new_request_cannot_reuse_an_old_uploaded_result(self):
        data = json.dumps([self.card('222222222','ML-9108R',10000)]).encode()
        stage_apify_file(SimpleUploadedFile('old.json', data), self.user)
        with self.assertRaises(ValidationError):
            stage_apify_file(SimpleUploadedFile('old.json',data), self.user, request_id=self.saved.pk)

    def test_http_upload_binds_request_and_does_not_resume_old_interrupted_session(self):
        url = reverse('procurement:selection_rules')
        response = self.client.post(url, {'action':'begin_apify_chunk_upload', 'supplier': self.phaeton.pk,
            'request_id':self.saved.pk, 'filename':'new-results.json','total':1,'digest':'c'*64})
        self.assertEqual(response.status_code, 200, response.content)
        session = Activity.objects.get(pk=response.json()['session_id'])
        self.assertEqual(session.details['selection_request_id'], self.saved.pk)
        self.assertEqual(session.details['next_batch'], 0)
        bad = self.client.post(url, {'action':'begin_apify_chunk_upload', 'supplier':self.omega.pk,
            'request_id':self.saved.pk, 'filename':'wrong.json','total':1,'digest':'d'*64})
        self.assertEqual(bad.status_code, 400)

    def test_new_snapshot_positive_stock_allows_existing_item_without_duplicates(self):
        source = audit_source('positive.json', self.saved.pk)
        import_apify_recheck_results([self.card('222222222','ML9108R',16000)],source,self.user,selection_request=self.saved)
        session = Activity.objects.create(user=self.user, action='apify_upload_session', details={
            'filename':'positive.json','audit_source':source,'next_batch':1,'total_batches':1})
        first = publish_profitable(session.pk,self.user)
        self.assertEqual(first['already'],1)
        PermanentBuyingItem.objects.get(offer=self.existing)
        again = publish_profitable(session.pk,self.user)
        self.assertEqual(again['added'],0)
        websession = self.client.session; websession['apify_buying_session_id']=session.pk; websession.save()
        page = self.client.get(reverse('procurement:selection_rules'), {'supplier': self.phaeton.pk})
        self.assertContains(page, 'Посмотреть товары')

    def test_actual_price_upload_starts_new_preparation_without_changing_old_catalog(self):
        from io import BytesIO
        from openpyxl import Workbook
        self.price.checked_on = date(2026,11,27); self.price.save(update_fields=['checked_on'])
        self.saved.details['checked_on'] = '2026-11-27'; self.saved.save()
        book = Workbook(); sheet = book.active
        sheet.append(['Артикул', 'Бренд', 'Номенклатура', 'Цена', 'Алматы СКЛАД'])
        sheet.append(['ML-9108R', 'MASUMA', 'Стойка стабилизатора', 7000, 3])
        data = BytesIO(); book.save(data)
        state = self.client.session
        state['apify_prepared_request_id'] = self.saved.pk
        state['apify_buying_session_id'] = 1234
        state.save()
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action':'upload_price', 'supplier':self.phaeton.pk, 'checked_on':'2026-11-28',
            'file':SimpleUploadedFile('new_price.xlsx', data.getvalue())})
        self.assertEqual(response.status_code, 302, str(response.context['price_form'].errors) if response.context else response.content[:300])
        self.assertNotIn('apify_prepared_request_id', self.client.session)
        self.assertNotIn('apify_buying_session_id', self.client.session)
        latest = PriceUpdate.objects.filter(supplier=self.phaeton).latest('pk')
        self.assertNotEqual(latest.pk, self.price.pk)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.cost, Decimal('3000'))
        self.assertEqual(self.existing.stock, '0')

    def test_old_processing_tab_cannot_process_new_current_upload(self):
        state = self.client.session; state['apify_buying_session_id']=1234; state.save()
        with patch('procurement.apify_batch_intake.process_next_apify_batch') as process:
            response = self.client.post(reverse('procurement:selection_rules'), {
                'action':'process_apify_batch','session_id':'999','supplier':self.phaeton.pk})
        self.assertEqual(response.status_code, 409)
        process.assert_not_called()

    def test_changed_source_price_hash_cannot_enter_a_new_upload(self):
        PriceUpdate.objects.filter(pk=self.price.pk).update(sha256='changed')
        with self.assertRaises(ValidationError):
            begin_chunked_upload(filename='fresh.json',total=1,digest='e'*64,
                user=self.user,request_id=self.saved.pk)

    def test_formatted_article_in_new_price_reuses_catalog_identity(self):
        PriceUpdate.objects.filter(pk=self.price.pk).update(rows=[dict(brand='Masuma', article='ML9108R',
            name='Стойка', category='Стойка', cost='6000', stock='5')])
        source = audit_source('case-change.json', self.saved.pk)
        import_apify_recheck_results([self.card('222222222','ML 9108R',16000)],source,self.user,selection_request=self.saved)
        self.assertEqual(KaspiPriceAudit.objects.get(source_filename=source).offer_id, self.existing.pk)
        self.assertEqual(Offer.objects.filter(supplier=self.phaeton).count(),2)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.cost, Decimal('3000'))
        self.assertEqual(self.existing.article,'ML-9108R')

    def test_conflicting_format_variants_rejected_before_export(self):
        batch = PriceUpdate.objects.create(supplier=self.phaeton,user=self.user,filename='conflict.xlsx',
            checked_on=date(2026,11,28),sha256='conflict-price',row_count=2,rows=[
                dict(brand='MASUMA',article='ML-9108R',name='Стойка',category='Стойка',cost='3000',stock='4'),
                dict(brand='MASUMA',article='ML9108R',name='Стойка',category='Стойка',cost='6000',stock='4')])
        with self.assertRaises(ValidationError):
            save_request(batch_id=batch.pk,categories=['Стойка'],minimum='2000',user=self.user)

    def test_new_results_require_explicit_or_remembered_selection(self):
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action':'begin_apify_chunk_upload','supplier':self.phaeton.pk,
            'filename':'unbound.json','total':1,'digest':'f'*64})
        self.assertEqual(response.status_code,400)
        self.assertFalse(Activity.objects.filter(action='apify_upload_session').exists())

    def test_new_request_aggregates_minimum_across_its_new_result_files(self):
        first = stage_apify_file(SimpleUploadedFile('part1.json',json.dumps([
            self.card('222222222','ML-9108R',18000)]).encode()),self.user,request_id=self.saved.pk)
        second = stage_apify_file(SimpleUploadedFile('part2.json',json.dumps([
            self.card('333333333','ML 9108R',10000)]).encode()),self.user,request_id=self.saved.pk)
        self.assertEqual(first.details['audit_source'],second.details['audit_source'])
        self.assertTrue(process_next_apify_batch(first.pk,self.user)['done'])
        self.assertTrue(process_next_apify_batch(second.pk,self.user)['done'])
        audit=KaspiPriceAudit.objects.get(source_filename=first.details['audit_source'])
        self.assertEqual(audit.min_price,Decimal('10000'))
        self.assertEqual(audit.cost_snapshot,Decimal('6000'))
        self.assertEqual(audit.price_evidence['source'],'part2.json')
        self.old.refresh_from_db()
        self.assertEqual(self.old.min_price,Decimal('2000'))

