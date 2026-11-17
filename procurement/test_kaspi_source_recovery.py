"""Resume bounded Kaspi work only when its original public source recovers."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from procurement.kaspi_identity_worker import (
    KaspiAccessDenied, resume_source_blocked_after_recovery,
)
from procurement.models import KaspiReviewTask, Offer, PriceUpdate, Supplier


class SourceRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('kaspi-recovery')
        self.supplier = Supplier.objects.create(name='Kaspi recovery supplier')
        self.offer = Offer.objects.create(
            supplier=self.supplier, brand='BRAND', article='F-1',
            name='BRAND Фильтр F-1', cost=Decimal('1000'),
            stock='3', kaspi_url='https://kaspi.kz/shop/p/filtr-123456/',
            kaspi_price=Decimal('2000'), match_verified=False,
            kaspi_price_checked_on=date.today()-timedelta(days=15),
        )
        batch = PriceUpdate.objects.create(
            supplier=self.supplier, user=self.user, filename='test.csv',
            sha256='recovery-test', checked_on=date.today(), status='done')
        self.task = KaspiReviewTask.objects.create(
            offer=self.offer,batch=batch,status='blocked',
            reason='Источник Kaspi ограничил доступ. Автоматическая проверка остановлена.')
        cache.clear()

    def valid_page(self):
        return '<script type="application/ld+json">' + json.dumps({
            '@type': 'Product', 'brand': 'BRAND', 'mpn': 'F-1',
            'name': 'BRAND Фильтр F-1',
        }) + '</script>'

    def test_one_successful_probe_unblocks_only_checks_without_faking_prices(self):
        reader = Mock(return_value=self.valid_page())
        self.assertEqual(resume_source_blocked_after_recovery(reader=reader), 1)
        self.task.refresh_from_db()
        self.offer.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')
        self.assertFalse(self.offer.match_verified)
        self.assertEqual(self.offer.kaspi_price_checked_on,
                         date.today()-timedelta(days=15))
        self.assertEqual(resume_source_blocked_after_recovery(reader=reader), 0)
        reader.assert_called_once_with(self.offer.kaspi_url)

    def test_denied_or_incomplete_response_keeps_queue_blocked(self):
        for response in (KaspiAccessDenied('denied'), '<html>No product metadata</html>'):
            cache.clear()
            reader = Mock(side_effect=response if isinstance(response, Exception) else None)
            if not isinstance(response, Exception):
                reader.return_value = response
            self.assertEqual(resume_source_blocked_after_recovery(reader=reader), 0)
            self.task.refresh_from_db()
            self.assertEqual(self.task.status, 'blocked')
            self.assertEqual(resume_source_blocked_after_recovery(reader=reader), 0)
            reader.assert_called_once()
