"""Regression tests: fifth 100 SKU review batch remains distinct and editable."""
from django.test import SimpleTestCase
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS

class FifthBatchIntegrityTests(SimpleTestCase):
    def test_fifth_batch_is_100_new_skus(self):
        prior = set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                    NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS)
        self.assertEqual(len(FIFTH_HUNDRED_OFFER_IDS), 100)
        self.assertEqual(len(set(FIFTH_HUNDRED_OFFER_IDS)), 100)
        self.assertFalse(prior.intersection(FIFTH_HUNDRED_OFFER_IDS))
        self.assertEqual(len(prior | set(FIFTH_HUNDRED_OFFER_IDS)), 360)

    def test_screenshot_controls_cover_published_scope(self):
        self.assertEqual(set(COMPARISON_BATCH_OFFER_IDS),
                         set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                             NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                             FIFTH_HUNDRED_OFFER_IDS))
