from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Supplier, PriceUpdate, Offer, Preferences, KaspiPriceAudit, Membership
from .omega_selection_requests import save_request, request_history, get_display_history
from .apify_price_recheck import import_apify_recheck_results


class MultiSupplierPreparationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('supplier-preparation')
        Membership.objects.create(user=self.user, role='admin')
        self.omega = Supplier.objects.create(name='Omega')
        self.phaeton = Supplier.objects.create(name='Phaeton DC')
        Preferences.objects.create(user=self.user, commission=Decimal('10'), expenses=Decimal('500'))
        self.client.force_login(self.user)

    def batch(self, supplier):
        return PriceUpdate.objects.create(supplier=supplier, user=self.user,
            filename='price.xlsx', checked_on=date.today(), sha256=str(supplier.pk),
            rows=[dict(brand='555', article='SA-1', name='Рычаг', category='Рычаг', cost='6000', stock='5')], row_count=1)

    def test_independent_numbering_history_and_page(self):
        omega_batch = self.batch(self.omega)
        phaeton_batch = self.batch(self.phaeton)
        for supplier, batch, expected in ((self.omega, omega_batch, 2), (self.phaeton, phaeton_batch, 1)):
            saved = save_request(batch_id=batch.pk, categories=['Рычаг'], minimum='2000', user=self.user)
            self.assertEqual(saved.details['number'], expected)
            self.assertEqual(list(request_history(supplier)), [saved])
        self.assertEqual(len(get_display_history(request_history(self.phaeton), include_legacy=False)), 1)
        page = self.client.get(reverse('procurement:selection_rules'), {'supplier': self.phaeton.pk})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['omega_price_batch'].pk, phaeton_batch.pk)
        self.assertEqual(page.context['price_batches'].paginator.count, 1)
        self.assertNotContains(page, 'Запрос №2')

    def test_same_sku_has_separate_cost_and_profit_audits(self):
        for supplier, cost in ((self.omega, '6000'), (self.phaeton, '7000')):
            Offer.objects.create(supplier=supplier, brand='555', article='SA-1', name='Рычаг', cost=cost, stock='5')
        raw = [dict(id='123456789', brand='555', title='555 SA-1 Рычаг',
                    url='https://kaspi.kz/shop/p/555-sa-1-123456789/',
                    offers=[{'price': 20000, 'sellerName': 'shop'}], searchQuery='555 SA-1')]
        stats = import_apify_recheck_results(raw, 'shared.json', self.user)
        self.assertEqual(stats['recheck_offer_updates'], 2)
        audits = list(KaspiPriceAudit.objects.order_by('cost_snapshot'))
        self.assertEqual([x.cost_snapshot for x in audits], [Decimal('6000'), Decimal('7000')])
        self.assertEqual(audits[0].profit - audits[1].profit, Decimal('1000'))
        self.assertEqual(Offer.objects.get(supplier=self.omega).cost, Decimal('6000'))

    def test_cannot_save_other_supplier_batch_from_selected_page(self):
        batch = self.batch(self.omega)
        response = self.client.post(reverse('procurement:selection_rules'), {
            'action': 'preview_omega_request', 'supplier': self.phaeton.pk,
            'price_batch_id': batch.pk, 'omega_category': ['Рычаг'],
        })
        self.assertEqual(response.status_code, 400)

    def test_native_phaeton_import_keeps_metadata_and_distinct_skus(self):
        from openpyxl import Workbook
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .price_updates import prepare_rows
        book = Workbook(); sheet = book.active
        sheet.append(['Артикул', 'Бренд', 'Номенклатура', 'Цена без скидки', 'Цена', 'Алматы СКЛАД', 'Штрихкод', 'Код ТНВЭД'])
        sheet.append([None, None, None, None, None, 'Количество'])
        sheet.append(['A1', '555', 'Рычаг', 9999, 6000, 3, '0012345', '8708809909'])
        sheet.append(['A2', '555', 'Колодки', 8888, 4000, 0, '0098765', '8708309909'])
        output = BytesIO(); book.save(output)
        rows = prepare_rows(SimpleUploadedFile('phaeton.xlsx', output.getvalue()), infer_categories=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['cost'], '6000')
        self.assertEqual(rows[0]['_barcode'], '0012345')
        self.assertEqual(rows[0]['category'], 'Рычаг')
        self.assertEqual(rows[1]['stock'], '0')

    def test_detail_highlights_lowest_available_supplier_price(self):
        expensive = Offer.objects.create(supplier=self.omega, brand='555', article='A', name='Рычаг', cost=7000, stock='3')
        Offer.objects.create(supplier=self.phaeton, brand='555', article='A', name='Рычаг', cost=6000, stock='4')
        page = self.client.get(reverse('procurement:detail', args=[expensive.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Минимальная закупочная цена в наличии', count=1)
        self.assertEqual([x.supplier_id for x in page.context['alternatives']], [self.phaeton.pk, self.omega.pk])

    def test_only_verified_unchanged_fresh_audits_avoid_repeat_queries(self):
        from .omega_selection_requests import fresh_checked_keys
        offer = Offer.objects.create(supplier=self.phaeton, brand='555', article='SA-1', name='Рычаг', cost=6000, stock='5')
        raw = [dict(id='123456789', brand='555', title='555 SA-1 Рычаг',
                    url='https://kaspi.kz/shop/p/555-sa-1-123456789/', offers=[{'price': 20000}])]
        import_apify_recheck_results(raw, 'fresh.json', self.user)
        batch = self.batch(self.phaeton)
        self.assertFalse(fresh_checked_keys(batch))
        offer.match_verified = True; offer.kaspi_url = raw[0]['url']; offer.save()
        self.assertEqual(len(fresh_checked_keys(batch)), 1)
        offer.cost = 7000; offer.save()
        self.assertFalse(fresh_checked_keys(batch))

    def test_large_price_storage_preserves_rows_and_boundaries(self):
        from .price_source import encode_price_rows, stored_rows_slice, iter_price_rows
        from .price_updates import advance_update
        rows = [dict(brand='555', article=f'A{i:04}', name='Рычаг №' + str(i),
                     cost='6000', stock='5', min_order=1, category='Рычаг',
                     _barcode='001234', _tnved='8708809909') for i in range(2001)]
        stored = encode_price_rows(rows)
        self.assertLess(len(str(stored)), len(str(rows)))
        self.assertEqual(list(stored_rows_slice(stored, 499, 701)), rows[499:701])
        batch = PriceUpdate.objects.create(supplier=self.phaeton, user=self.user,
            filename='large.xlsx', checked_on=date.today(), sha256='large',
            rows=stored, row_count=len(rows), cursor=499,
            summary={k: 0 for k in ('new','changed','unchanged','conflicts','reused','queued','unavailable')})
        self.assertEqual(list(iter_price_rows(batch.pk)), rows)
        self.assertEqual(list(iter_price_rows(batch.pk, fields=['article']))[500], {'article': 'A0500'})
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            advanced = advance_update(batch.pk)
        self.assertLessEqual(len(queries), 40)
        self.assertEqual(advanced.cursor, 699)
        self.assertEqual(Offer.objects.filter(supplier=self.phaeton).count(), 200)
        self.assertTrue(Offer.objects.filter(supplier=self.phaeton, article='A0499').exists())
