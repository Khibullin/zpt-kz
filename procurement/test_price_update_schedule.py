from datetime import datetime, timezone

from django.test import SimpleTestCase

from procurement.management.commands.process_price_updates import photo_window_open


class PhotoWindowTests(SimpleTestCase):
    def test_weekend_day_is_open(self):
        self.assertTrue(photo_window_open(datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc)))

    def test_weekday_day_is_closed(self):
        self.assertFalse(photo_window_open(datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)))

    def test_weekday_night_is_open(self):
        self.assertTrue(photo_window_open(datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)))
