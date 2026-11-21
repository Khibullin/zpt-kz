"""Regression coverage: Omega prices are dates; Apify observations are timestamps."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from django.test import SimpleTestCase
from .eep_comparison import is_price_stale


class ComparisonPriceDateTests(SimpleTestCase):
    def test_omega_date_does_not_raise_type_error(self):
        today = date(2026, 11, 21)
        self.assertFalse(is_price_stale(today, today=today))
        self.assertFalse(is_price_stale(today - timedelta(days=7), today=today))
        self.assertTrue(is_price_stale(today - timedelta(days=8), today=today))

    def test_apify_timestamp_does_not_raise_type_error(self):
        today = date(2026, 11, 21)
        recent = datetime(2026, 11, 20, 3, 42, tzinfo=dt_timezone.utc)
        old = datetime(2026, 11, 1, 3, 42, tzinfo=dt_timezone.utc)
        self.assertFalse(is_price_stale(recent, today=today))
        self.assertTrue(is_price_stale(old, today=today))

    def test_missing_date_requires_refresh(self):
        self.assertTrue(is_price_stale(None, today=date(2026, 11, 21)))
