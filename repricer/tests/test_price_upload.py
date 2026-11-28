from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from repricer.models import KaspiActiveExportRow, KaspiManualPriceDraft, KaspiActiveCompetitorOffer, KaspiActiveCityCheck
from django.utils import timezone
from procurement.models import Activity
from repricer.price_upload import HEADERS
from repricer.tests import test_apify_output as output_tests


class KaspiPriceUploadTests(TestCase):
    setUp = output_tests.ApifyOutputTests.setUp

    def verified(self, row, price):
        now = timezone.now()
        for city in ('Almaty', 'Astana'):
            KaspiActiveCompetitorOffer.objects.update_or_create(active_row=row, city=city,
                seller_id='test-other', defaults={'price':price,'checked_at':now})
            KaspiActiveCityCheck.objects.update_or_create(active_row=row, city=city,defaults={'checked_at':now})
        row.competitor_checked_at = now
        row.competitor_scan_status = 'MATCHED'
        row.competitor_min_price = price
        row.save()
        Activity.objects.create(user=self.staff,action='repricer_price_floor',
            details={'batch_id':self.batch.pk,'row_id':row.pk,'price':1500})

    def ready(self):
        self.verified(self.active, 2201)
        self.active.competitor_min_price = 2201
        self.active.competitor_scan_status = 'MATCHED'
        self.active.save()
        return self.client.get(reverse('repricer:prepare_prices'))

    def file(self, duplicate=False, current=2700, headers=HEADERS):
        wb = Workbook()
        wb.active.append(list(headers))
        wb.active.append(['1017110XEN01', '=label', 'Brand', current, 7, 'yes', None, 'no', 0, 3])
        wb.active.append(['UNMAPPED', 'Untouched', 'Other', 5000, 0, 2, None, None, None, None])
        wb.active.append(['00123', 'Leading zero', 'Brand', 900, 4, 0, None, None, None, None])
        if duplicate:
            wb.active.append(['1017110XEN01', 'Duplicate', 'Brand', 2700])
        wb.create_sheet('Original notes').append(['Keep original notes'])
        stream = BytesIO()
        wb.save(stream)
        return SimpleUploadedFile('price-list.xlsx', stream.getvalue())

    def download(self, response=None, **kwargs):
        response = response or self.ready()
        data = {'plan_token': response.context['plan_token'], 'selected': [str(self.active.pk)],
                'kaspi_template': self.file()}
        data.update(kwargs)
        return self.client.post(reverse('repricer:prepare_prices'), data)

    def test_native_download_changes_only_selected_price_and_no_database_prices(self):
        response = self.ready()
        self.assertEqual(response.context['eligible'][0]['new'], 2134)
        output = self.download(response)
        self.assertEqual(output.status_code, 200)
        wb = load_workbook(BytesIO(output.content))
        original = load_workbook(self.file())
        self.assertEqual(wb.sheetnames, original.sheetnames)
        for old_sheet, sheet in zip(original, wb):
            for row in old_sheet:
                for cell in row:
                    expected = 2134 if old_sheet == original.worksheets[0] and cell.coordinate == 'D2' else cell.value
                    self.assertEqual(sheet[cell.coordinate].value, expected)
        self.active.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.active.price, 2700)
        self.assertEqual(self.listing.last_known_our_price, 2700)
        self.assertFalse(KaspiManualPriceDraft.objects.exists())
        self.assertIn('attachment;', output['Content-Disposition'])

    def test_unconfirmed_and_unmapped_never_selected(self):
        response = self.ready()
        self.assertEqual(len(response.context['eligible']), 1)
        self.assertEqual(response.context['skipped'], 1)
        self.active.competitor_scan_status = 'NOT_CONFIRMED'
        self.active.save()
        response = self.client.get(reverse('repricer:prepare_prices'))
        self.assertFalse(response.context['eligible'])

    def test_reject_changed_monitoring_after_preview(self):
        response = self.ready()
        self.verified(self.active, 1900)
        output = self.download(response)
        self.assertEqual(output.status_code, 400)
        self.assertContains(output, 'Данные контроля цен изменились', status_code=400)

    def test_reject_bad_template_duplicate_sku_and_conflicting_price(self):
        response = self.ready()
        for file in [self.file(headers=('Wrong',) + HEADERS[1:]), self.file(duplicate=True), self.file(current=2800)]:
            output = self.download(response, kaspi_template=file)
            self.assertEqual(output.status_code, 400)
            self.assertNotIn('Content-Disposition', output)

    def test_reject_empty_selection_and_tampered_token(self):
        response = self.ready()
        for overrides in [{'selected': []}, {'plan_token': 'tampered'}, {'selected': [str(self.unknown.pk)]}]:
            self.assertEqual(self.download(response, **overrides).status_code, 400)

    def test_reject_duplicate_source_and_no_stock(self):
        self.ready()
        KaspiActiveExportRow.objects.create(batch=self.batch, row_number=4,
            master_sku=self.active.master_sku, status='DUPLICATE_MASTER_SKU')
        response = self.client.get(reverse('repricer:prepare_prices'))
        self.assertFalse(response.context['eligible'])

    def test_all_pages_are_in_plan(self):
        self.ready()
        KaspiActiveExportRow.objects.bulk_create([
            KaspiActiveExportRow(batch=self.batch, row_number=i+5, master_sku=f'unknown-{i}', status='MISSING_LISTING')
            for i in range(25)
        ])
        response = self.client.get(reverse('repricer:prepare_prices') + '?page=2&city=Almaty')
        self.assertEqual(len(response.context['plan']), 27)

    def test_staff_only_and_csrf(self):
        self.client.force_login(User.objects.create_user('ordinary'))
        self.assertEqual(self.client.get(reverse('repricer:prepare_prices')).status_code, 302)
        self.client.force_login(self.staff)
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(reverse('repricer:prepare_prices'), {}).status_code, 403)

    def test_confirmed_competitor_can_lower_unlinked_active_sku(self):
        self.ready()
        self.verified(self.unknown, 4000)
        self.unknown.competitor_scan_status = 'MATCHED'
        self.unknown.competitor_min_price = 4000
        self.unknown.save()
        response = self.client.get(reverse('repricer:prepare_prices'))
        eligible = {item['sku']: item for item in response.context['eligible']}
        self.assertEqual(eligible['UNMAPPED']['new'], 3880)
        output = self.download(response, selected=[str(self.unknown.pk)])
        self.assertEqual(output.status_code, 200)
        wb = load_workbook(BytesIO(output.content))
        self.assertEqual(wb.active['D3'].value, 3880)
        self.assertEqual(wb.active['D2'].value, 2700)
        self.unknown.refresh_from_db()
        self.assertIsNone(self.unknown.listing_id)
        self.assertEqual(self.unknown.price, 5000)

    def test_export_skips_equal_or_higher_competitors_and_never_raises_price(self):
        for minimum in (2700, 2701, 2800, 5000):
            with self.subTest(minimum=minimum):
                self.active.competitor_scan_status = 'MATCHED'
                self.active.competitor_min_price = minimum
                self.active.save()
                response = self.client.get(reverse('repricer:prepare_prices'))
                self.assertFalse(response.context['eligible'])

    def test_upload_defense_rejects_a_price_increase(self):
        from repricer.price_upload import update_price_file
        with self.assertRaisesMessage(ValueError, 'только снижение'):
            update_price_file(self.file(), [{'sku': '1017110XEN01', 'current': 2700, 'new': 2800}])

