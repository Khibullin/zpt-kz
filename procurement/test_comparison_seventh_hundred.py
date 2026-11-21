"""Seventh batch count, exact-article guard and clipboard availability."""
from django.test import SimpleTestCase
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_seventh_hundred import SEVENTH_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class SeventhBatchIntegrityTests(SimpleTestCase):
    def test_no_repeat(self):
        old = set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                  NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                  FIFTH_HUNDRED_OFFER_IDS + SIXTH_HUNDRED_OFFER_IDS)
        self.assertEqual(len(old), 460)
        self.assertEqual(len(SEVENTH_HUNDRED_OFFER_IDS), 100)
        self.assertFalse(old & set(SEVENTH_HUNDRED_OFFER_IDS))
        self.assertEqual(len(set(COMPARISON_BATCH_OFFER_IDS)), 560)

    def test_screenshot_scope(self):
        self.assertEqual(set(SEVENTH_HUNDRED_OFFER_IDS) - set(COMPARISON_BATCH_OFFER_IDS), set())
