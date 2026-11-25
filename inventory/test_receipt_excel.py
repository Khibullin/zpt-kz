from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from openpyxl import Workbook

from .receipt_excel import parse_receipt
from .models import Item, GoodsDocument, StockEntry, DocumentAttachment

HEADER = ['', 'Артикул', 'Наименование', 'Бренд', 'Описание', 'Модель',
          'OEM', 'Кол-во', 'Цена без скидки', 'Цена со скидкой', 'Сумма со скидкой']


def product(article='001', quantity=2, cost='100.02', brand='EEP', name='Деталь'):
    return ['', article, name, brand, '', 'Toyota', 'OEM', quantity, 110, cost, '200.04']


def upload(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    data = BytesIO()
    book.save(data)
    return SimpleUploadedFile('original.xlsx', data.getvalue())


class ReceiptParsingTests(SimpleTestCase):
    def test_grouped_invoice_preserves_products_and_reports_totals(self):
        rows = [HEADER, [], product(), ['', '', '', '', '', '', 'Итого, Toyota:', 2, '', '', 200.04],
                ['', 'Toyota'], HEADER, product('002', 1, '50.01'),
                ['', '', '', '', '', '', 'Сумма всего:', '', '', '', 250]]
        parsed = parse_receipt(upload(rows))
        self.assertEqual([row['article'] for row in parsed['rows']], ['001', '002'])
        self.assertEqual([row['source_row'] for row in parsed['rows']], [3, 7])
        self.assertEqual([row['source_row'] for row in parsed['skipped']], [4, 5, 6, 8])
        self.assertFalse(any(row['errors'] for row in parsed['rows']))
        self.assertEqual(parsed['totals'], {'declared': Decimal('250'), 'calculated': Decimal('250.05'), 'difference': Decimal('.05')})

    def test_partial_bad_and_unclassified_rows_stay_visible(self):
        parsed = parse_receipt(upload([HEADER, product(brand=''), product('002', 'bad', 'NaN'),
                                       ['', 'UNKNOWN'], ['', '', '', '', '', '', 'Неизвестная строка']]))
        self.assertEqual(len(parsed['rows']), 4)
        self.assertTrue(all(row['errors'] for row in parsed['rows']))
        self.assertEqual(parsed['rows'][1]['packages'], 'bad')
        self.assertIn('Неизвестная строка', parsed['rows'][3]['source_values'])
        self.assertNotIn('calculated', parsed['totals'])

    def test_product_named_total_and_group_article_are_not_dropped(self):
        parsed = parse_receipt(upload([HEADER, product(name='Итого'), product(article='Toyota'),
                                     ['', '', '', '', '', '', 'Итого, Toyota:', 4]]))
        self.assertEqual(len(parsed['rows']), 2)
        self.assertEqual(len(parsed['skipped']), 1)

    def test_xls_uses_same_group_recognition_and_does_not_change_bytes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        rows = [HEADER, product(), ['', '', '', '', '', '', 'Итого, Toyota:', 2]]
        sheet = SimpleNamespace(nrows=len(rows), ncols=11, row_values=lambda i: rows[i])
        book = Mock()
        book.sheet_by_index.return_value = sheet
        source = SimpleUploadedFile('original.xls', b'original supplier bytes')
        with patch('xlrd.open_workbook', return_value=book):
            parsed = parse_receipt(source)
        self.assertEqual(len(parsed['rows']), 1)
        self.assertEqual(source.read(), b'original supplier bytes')
        book.release_resources.assert_called_once()

    def test_limits_and_missing_header_still_block_invalid_files(self):
        for rows in [[['Unrecognized']], [HEADER], [HEADER] + [product()] * 301]:
            with self.assertRaises(ValidationError):
                parse_receipt(upload(rows))


class ReceiptPreviewTests(TestCase):
    def test_preview_keeps_errors_and_creates_no_accounting_operations(self):
        user = get_user_model().objects.create_superuser(username='invoice-preview', password='test')
        self.client.force_login(user)
        item = Item.objects.create(code='preview', article='001', brand='EEP', name='Деталь', unit='piece')
        response = self.client.post(reverse('inventory:goods_new', args=['receipt']),
                                    {'action': 'parse_excel', 'file': upload([HEADER, product(), product('002', brand='')])})
        self.assertEqual(response.status_code, 200)
        rows = response.json()['rows']
        self.assertEqual(rows[0]['item'], item.pk)
        self.assertTrue(rows[1]['errors'])
        self.assertNotIn('item', rows[1])
        self.assertEqual(Item.objects.count(), 1)
        for model in (GoodsDocument, StockEntry, DocumentAttachment):
            self.assertFalse(model.objects.exists())
