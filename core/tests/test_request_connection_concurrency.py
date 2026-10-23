"""Actual PostgreSQL row locks; intentionally skipped on SQLite."""
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from core.models import Request, Seller, Match, SellerRequestAccess, RequestInteraction
from core.services.request_connections import participate


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(TWO_WAY_REQUESTS_ENABLED=True)
class RequestConnectionConcurrencyTests(TransactionTestCase):
    def test_simultaneous_first_interests_reuse_one_access(self):
        user = get_user_model().objects.create_user(username='concurrent')
        seller = Seller.objects.create(user=user, name='Магазин', whatsapp='77001112233', transport_type='car')
        req = Request.objects.create(phone='77002223344', public_summary='Наружный ШРУС', seller_feed_enabled=True,
            contact_consent_at=timezone.now())

        def respond():
            close_old_connections()
            try:
                return participate(Request.objects.get(pk=req.pk), Seller.objects.get(pk=seller.pk)).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(respond) for _ in range(2)]
            ids = [future.result(timeout=15) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Match.objects.filter(request=req, seller=seller).count(), 1)
        self.assertEqual(SellerRequestAccess.objects.filter(request=req, seller=seller).count(), 1)
        self.assertEqual(RequestInteraction.objects.filter(request=req, seller=seller, kind='interest').count(), 1)
