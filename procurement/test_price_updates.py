from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import (Supplier, Offer, PriceUpdate, KaspiReviewTask, Membership,
                     KaspiSellerSnapshot)
from .price_updates import stage_update, advance_update
from .importing import import_offers


class PriceUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('price-admin')
        Membership.objects.create(user=self.user, role='admin')
        self.supplier = Supplier.objects.create(name='Omega')
        self.today = timezone.localdate()
        self.offer = Offer.objects.create(supplier=self.supplier, brand='BRAND', article='001A',
            name='Фильтр', cost=1000, stock='2', match_verified=True,
            supplier_checked_on=self.today-timedelta(days=2),
            kaspi_price=5000, kaspi_price_checked_on=self.today-timedelta(days=6),
            kaspi_url='https://kaspi.kz/shop/p/filter-123/')

    def file(self, rows='BRAND;001A;Фильтр;900;5\n', header='brand;article;name;cost;stock\n'):
        return SimpleUploadedFile('prices.csv', (header + rows).encode('utf-8'))

    def stage(self, **kwargs):
        return stage_update(self.file(), self.supplier, self.user, self.today, **kwargs)

    def test_supplier_update_does_not_renew_kaspi_or_reverify_identity(self):
        before = self.offer.kaspi_price_checked_on
        batch = advance_update(self.stage().pk)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.cost, Decimal(900))
        self.assertEqual(self.offer.kaspi_price_checked_on, before)
        self.assertEqual(self.offer.supplier_checked_on, self.today)
        self.assertTrue(self.offer.match_verified)
        self.assertEqual(batch.summary['changed'], 1)
        self.assertEqual(batch.status, 'done')

    def test_resume_is_idempotent_and_missing_stock_only_finalized_at_end(self):
        batch = stage_update(self.file('BRAND;OTHER;Колодки;500;4\nBRAND;THIRD;Диск;700;2\n'),
            self.supplier, self.user, self.today, True)
        with patch('procurement.price_updates.BATCH_SIZE', 1):
            first = advance_update(batch.pk)
            self.offer.refresh_from_db()
            self.assertEqual(first.cursor, 1)
            self.assertEqual(self.offer.stock, '2')
            final = advance_update(batch.pk)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.stock, '0')
        self.assertEqual(final.summary['unavailable'], 1)
        again = advance_update(batch.pk)
        self.assertEqual(again.summary, final.summary)
        self.assertEqual(Offer.objects.filter(supplier=self.supplier).count(), 3)

    def test_partial_preserves_absent_items_and_other_suppliers(self):
        other = Supplier.objects.create(name='Other')
        same = Offer.objects.create(supplier=other, brand='BRAND', article='001A', name='Фильтр', cost=10, stock='9')
        batch = stage_update(self.file('BRAND;NEW;Диск;700;0\n'), self.supplier, self.user, self.today)
        advance_update(batch.pk)
        same.refresh_from_db(); self.offer.refresh_from_db()
        self.assertEqual(same.stock, '9')
        self.assertEqual(self.offer.stock, '2')
        self.assertFalse(KaspiReviewTask.objects.exists())

    def test_invalid_late_row_never_changes_catalogue(self):
        with self.assertRaises(ValueError):
            stage_update(self.file('BRAND;001A;Фильтр;900;5\nBRAND;OTHER;Диск;bad;3\n'), self.supplier, self.user, self.today)
        self.assertFalse(PriceUpdate.objects.exists())
        self.offer.refresh_from_db(); self.assertEqual(self.offer.cost, 1000)

    def test_duplicate_or_older_active_upload_rejected(self):
        self.stage()
        with self.assertRaises(ValueError): self.stage()
        self.assertEqual(PriceUpdate.objects.count(), 1)

    def test_missing_stock_column_rejected(self):
        with self.assertRaises(ValueError):
            stage_update(self.file('BRAND;001A;Фильтр;900\n', 'brand;article;name;cost\n'),
                         self.supplier, self.user, self.today)

    def test_name_change_invalidates_matching_and_creates_review(self):
        KaspiSellerSnapshot.objects.create(offer=self.offer, source_url=self.offer.kaspi_url, checked_on=self.today, complete=True)
        batch = stage_update(self.file('BRAND;001A;Фильтр комплект 2 шт;900;5\n'), self.supplier, self.user, self.today)
        advance_update(batch.pk)
        self.offer.refresh_from_db()
        self.assertFalse(self.offer.match_verified)
        self.assertFalse(KaspiSellerSnapshot.objects.exists())
        self.assertEqual(KaspiReviewTask.objects.get(offer=self.offer).status, 'pending')

    def test_new_supplier_reuses_exact_sourced_identity_without_renewing_dates(self):
        self.offer.characteristics = {'kaspi_identity': {'source_url': self.offer.kaspi_url,
            'confirmed': True, 'title': 'BRAND 001A Фильтр', 'article': '001A', 'brand': 'BRAND'}}
        self.offer.save()
        other = Supplier.objects.create(name='Phaeton')
        batch = stage_update(self.file(), other, self.user, self.today)
        final = advance_update(batch.pk)
        new = Offer.objects.get(supplier=other)
        self.assertTrue(new.match_verified)
        self.assertEqual(new.kaspi_url, self.offer.kaspi_url)
        self.assertEqual(new.kaspi_price_checked_on, self.offer.kaspi_price_checked_on)
        self.assertIsNone(new.sales_30)
        self.assertEqual(final.summary['reused'], 1)

    def test_legacy_import_supplier_only_does_not_renew_kaspi(self):
        before = self.offer.kaspi_price_checked_on
        import_offers(self.file(), self.supplier.name, self.today, self.user)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.kaspi_price_checked_on, before)

    def test_access_and_page(self):
        url = reverse('procurement:price_updates')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(url), 'Обновление прайс-листов')
        self.assertEqual(self.client.get(reverse('procurement:price_update_advance', args=[1])).status_code, 405)
        membership = Membership.objects.get(user=self.user); membership.role='user'; membership.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_conflicting_prices_are_quarantined_and_do_not_change_cost(self):
        batch = stage_update(self.file('BRAND;001A;Фильтр;900;5\nBRAND;001A;Фильтр;800;6\n'),
            self.supplier, self.user, self.today, True)
        final = advance_update(batch.pk)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.cost, Decimal(1000))
        self.assertEqual(self.offer.stock, '')
        self.assertEqual(final.summary['conflicts'], 1)
        self.assertEqual(final.summary['unavailable'], 0)

    def test_native_phaeton_header_and_zero_stock(self):
        from io import BytesIO
        from openpyxl import Workbook
        from .price_updates import prepare_rows
        book = Workbook(); sheet = book.active
        sheet.append(['Артикул', 'Бренд', 'Номенклатура', 'Цена', 'Алматы СКЛАД'])
        sheet.append([None, None, None, None, 'Количество'])
        sheet.append(['001', 'PART', 'Диск', 1000, 0])
        output = BytesIO(); book.save(output)
        rows = prepare_rows(SimpleUploadedFile('phaeton.xlsx', output.getvalue()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['article'], '001')
        self.assertEqual(rows[0]['stock'], '0')

    def test_refresh_is_complete_and_does_not_change_supplier_date(self):
        from .kaspi_refresh import refresh_task
        batch = advance_update(self.stage().pk)
        task, _ = KaspiReviewTask.objects.update_or_create(offer=self.offer, defaults={'batch':batch, 'reason':'Обновить', 'status':'pending'})
        sellers = [{'merchant_id':'shop', 'name':'Shop', 'price':'6000',
                    'url':'https://kaspi.kz/shop/m/shop/', 'rating':None, 'reviews':None}]
        self.assertTrue(refresh_task(task.pk, reader=lambda url: sellers))
        self.offer.refresh_from_db(); task.refresh_from_db()
        self.assertEqual(self.offer.kaspi_price, Decimal(6000))
        self.assertEqual(self.offer.kaspi_price_checked_on, self.today)
        self.assertEqual(self.offer.supplier_checked_on, self.today)
        self.assertEqual(task.status, 'done')

    def test_refresh_access_denied_preserves_previous_price(self):
        from .kaspi_refresh import refresh_task
        batch = advance_update(self.stage().pk)
        task, _ = KaspiReviewTask.objects.update_or_create(offer=self.offer, defaults={'batch':batch, 'reason':'Обновить', 'status':'pending'})
        def denied(url): raise ValueError('HTTP 403')
        self.assertFalse(refresh_task(task.pk, reader=denied))
        self.offer.refresh_from_db(); task.refresh_from_db()
        self.assertEqual(self.offer.kaspi_price, Decimal(5000))
        self.assertEqual(task.status, 'blocked')
