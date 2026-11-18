from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import Membership, Offer, PriceUpdate, PurchaseOrder, Selection, Supplier, WholesaleSelectionRule


class PurchaseListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('list-manager', first_name='Рашид', last_name='Хайбуллин')
        cls.other = get_user_model().objects.create_user('other-list-manager')
        cls.outsider = get_user_model().objects.create_user('list-outsider')
        Membership.objects.create(user=cls.user, role='admin')
        cls.omega = Supplier.objects.create(name='Omega')
        cls.phaeton = Supplier.objects.create(name='Фаетон')
        cls.first = Offer.objects.create(supplier=cls.omega, brand='WXQP', article='00001',
            name='Подушка двигателя', cost=Decimal('2247'), stock='4')
        cls.second = Offer.objects.create(supplier=cls.phaeton, brand='=FORMULA', article='=2+2',
            name='Комплект ' + 'деталь ' * 50, cost=Decimal('100'), stock='9', characteristics={'unit': 'компл.'})
        cls.private = Offer.objects.create(supplier=cls.omega, brand='SECRET', article='PRIVATE',
            name='Другой личный список', cost=Decimal('99999'), stock='2')
        Selection.objects.create(user=cls.user, offer=cls.first, quantity=3)
        Selection.objects.create(user=cls.user, offer=cls.second, quantity=2)
        Selection.objects.create(user=cls.other, offer=cls.private, quantity=1)

    def setUp(self):
        self.client.force_login(self.user)

    def book(self, **params):
        response = self.client.get(reverse('procurement:basket_excel'), params)
        self.assertEqual(response.status_code, 200)
        self.assertIn('.xlsx', response['Content-Disposition'])
        return load_workbook(BytesIO(b''.join(response.streaming_content)))

    def test_export_has_complete_private_list_identity_and_correct_totals(self):
        before = list(Selection.objects.values_list('user_id', 'offer_id', 'quantity'))
        sheet = self.book().active
        self.assertEqual(sheet.title, 'Список закупки')
        self.assertIn('СПИСОК ЗАКУПКИ', sheet['A1'].value)
        self.assertIn('ТОО «Азия Грейт»', sheet['A3'].value)
        self.assertIn('120140021357', sheet['A3'].value)
        self.assertIn('Рашид Хайбуллин', sheet['A4'].value)
        self.assertIn('Мурат', sheet['A5'].value)
        self.assertIn('rkhaibullin@gmail.com', sheet['A6'].value)
        self.assertEqual(sheet['D11'].value, '00001')
        self.assertEqual(sheet['D11'].data_type, 's')
        self.assertEqual(sheet['G11'].value, 3)
        self.assertEqual(sheet['H11'].value, 2247)
        self.assertEqual(sheet['I11'].value, 6741)
        self.assertEqual(sheet['F12'].value, 'компл.')
        self.assertEqual(sheet['I12'].value, 200)
        totals = [row for row in sheet.iter_rows() if row[0].value == 'ОБЩАЯ СУММА ЗАКУПКИ']
        self.assertEqual(totals[0][8].value, 6941)
        self.assertFalse(any(cell.value == 'PRIVATE' for row in sheet for cell in row))
        self.assertEqual(list(Selection.objects.values_list('user_id', 'offer_id', 'quantity')), before)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.first.refresh_from_db()
        self.assertEqual((self.first.cost, self.first.stock), (Decimal('2247'), '4'))

    def test_export_has_no_text_formulas_and_print_layout(self):
        sheet = self.book(buyer='=HYPERLINK("example")').active
        self.assertEqual(sheet['D12'].value, '=2+2')
        self.assertEqual(sheet['D12'].data_type, 's')
        self.assertEqual(sheet['C12'].data_type, 's')
        self.assertFalse(any(cell.data_type == 'f' for row in sheet for cell in row))
        self.assertEqual(sheet.freeze_panes, 'E11')
        self.assertEqual(sheet.page_setup.orientation, 'landscape')
        self.assertEqual(sheet.page_setup.fitToWidth, 1)
        self.assertTrue(sheet.print_area)
        self.assertEqual(sheet.auto_filter.ref, 'A10:I12')
        self.assertEqual(sheet['I11'].number_format, '#,##0.00')

    def test_list_navigation_editable_quantities_and_no_duplicate_top_link(self):
        response = self.client.get(reverse('procurement:basket'))
        self.assertContains(response, 'Скачать Excel')
        self.assertContains(response, 'Рашид Хайбуллин')
        self.assertContains(response, 'data-basket-count', count=1)
        self.assertNotContains(response, 'Обновление прайсов')
        self.assertNotContains(response, 'Поставщик<br>')
        self.assertNotContains(response, 'Другой личный список')
        selected = Selection.objects.get(user=self.user, offer=self.first)
        self.client.post(reverse('procurement:select', args=[self.first.pk]), {'quantity': 1000})
        selected.refresh_from_db()
        self.assertEqual(selected.quantity, 1000)
        self.assertContains(self.client.get(reverse('procurement:basket')), 'value="1000"')
        self.client.post(reverse('procurement:remove', args=[selected.pk]))
        self.assertFalse(Selection.objects.filter(pk=selected.pk).exists())
        self.assertTrue(Selection.objects.filter(user=self.other).exists())

    def test_download_requires_membership_and_rejects_invalid_buyer(self):
        url = reverse('procurement:basket_excel')
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get(url, {'buyer': 'x' * 201}).status_code, 400)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


class PreparationPriceUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('preparation-admin')
        Membership.objects.create(user=self.user, role='admin')
        self.supplier = Supplier.objects.create(name='Omega')
        self.client.force_login(self.user)
        self.url = reverse('procurement:selection_rules')

    def test_legacy_page_redirects_and_history_is_preserved_on_preparation(self):
        batch = PriceUpdate.objects.create(user=self.user, supplier=self.supplier, filename='old-source.csv',
            checked_on=timezone.localdate(), sha256='old-history', status='done', rows=[{'source': 'retained'}])
        self.assertRedirects(self.client.get(reverse('procurement:price_updates')), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, 'old-source.csv')
        self.assertContains(response, 'name="action" value="upload_price"')
        self.assertContains(response, 'id="price_supplier"')
        self.assertContains(response, 'id="pricelist-history"')
        self.assertNotContains(response, 'История обновлений</a>')
        batch.refresh_from_db()
        self.assertEqual(batch.rows, [{'source': 'retained'}])

    def test_inline_upload_uses_existing_staged_import_and_does_not_change_kaspi(self):
        offer = Offer.objects.create(supplier=self.supplier, brand='WXQP', article='001', name='Деталь',
            cost=Decimal('3000'), stock='4', kaspi_price=Decimal('6000'), match_verified=True,
            kaspi_url='https://kaspi.kz/shop/p/test-12345/', kaspi_price_checked_on=timezone.localdate()-timedelta(days=3))
        price_date = offer.kaspi_price_checked_on
        upload = SimpleUploadedFile('new.csv', 'brand;article;name;cost;stock\nWXQP;001;Деталь;3200;7\n'.encode())
        response = self.client.post(self.url, {'action': 'upload_price', 'supplier': self.supplier.pk,
            'checked_on': timezone.localdate().isoformat(), 'file': upload})
        self.assertRedirects(response, self.url)
        batch = PriceUpdate.objects.get()
        self.assertEqual(batch.filename, 'new.csv')
        self.assertFalse(batch.full_catalog)
        self.assertEqual(batch.status, 'ready')
        response = self.client.post(reverse('procurement:price_update_advance', args=[batch.pk]))
        self.assertEqual(response.status_code, 200)
        offer.refresh_from_db()
        self.assertEqual((offer.cost, offer.stock), (Decimal('3200'), '7'))
        self.assertEqual((offer.kaspi_price, offer.kaspi_price_checked_on), (Decimal('6000'), price_date))
        self.assertTrue(offer.match_verified)

    def test_invalid_upload_keeps_saved_rules_and_does_not_stage_file(self):
        rule = WholesaleSelectionRule.objects.create(singleton_key=1, min_cost=2345)
        response = self.client.post(self.url, {'action': 'upload_price', 'supplier': self.supplier.pk,
            'checked_on': timezone.localdate().isoformat(), 'file': SimpleUploadedFile('wrong.exe', b'wrong')})
        self.assertContains(response, 'Нужен XLSX или CSV / TSV.')
        self.assertFalse(PriceUpdate.objects.exists())
        rule.refresh_from_db()
        self.assertEqual(rule.min_cost, Decimal('2345'))

    def test_preparation_title_contains_no_forms_and_results_upload_is_in_body(self):
        response = self.client.get(self.url)
        class Page(HTMLParser):
            in_title = False
            title = ''
            body_uploads = 0
            def handle_starttag(self, tag, attrs):
                if tag == 'title':
                    self.in_title = True
                if tag == 'form' and dict(attrs).get('class') == 'buying-upload' and not self.in_title:
                    self.body_uploads += 1
            def handle_endtag(self, tag):
                if tag == 'title':
                    self.in_title = False
            def handle_data(self, data):
                if self.in_title:
                    self.title += data
        page = Page()
        page.feed(response.content.decode())
        self.assertEqual(page.title.strip(), 'Подготовка товаров к закупке · ZPT.KZ')
        self.assertEqual(page.body_uploads, 1)
