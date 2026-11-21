"""Regression: new batch 361–460 has no duplicates and includes clipboard tools."""
from django.test import SimpleTestCase
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class SixthBatchIntegrityTests(SimpleTestCase):
    def test_new_one_hundred_are_distinct(self):
        old = set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                  NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                  FIFTH_HUNDRED_OFFER_IDS)
        self.assertEqual(len(old), 360)
        self.assertEqual(len(SIXTH_HUNDRED_OFFER_IDS), 100)
        self.assertEqual(len(set(SIXTH_HUNDRED_OFFER_IDS)), 100)
        self.assertFalse(old.intersection(SIXTH_HUNDRED_OFFER_IDS))
        self.assertEqual(len(old | set(SIXTH_HUNDRED_OFFER_IDS)), 460)

    def test_screenshot_controls_cover_all_460(self):
        self.assertEqual(set(COMPARISON_BATCH_OFFER_IDS),
                         set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                             NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                             FIFTH_HUNDRED_OFFER_IDS + SIXTH_HUNDRED_OFFER_IDS))
