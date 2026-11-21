"""Regression coverage for the 200-item review queue after first 560 offers."""
from django.test import SimpleTestCase

from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_seventh_hundred import SEVENTH_HUNDRED_OFFER_IDS
from .comparison_eighth_two_hundred import EIGHTH_TWO_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class EighthTwoHundredIntegrityTests(SimpleTestCase):
    def test_eighth_batch_is_200_distinct_new_articles(self):
        prior = set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                    NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                    FIFTH_HUNDRED_OFFER_IDS + SIXTH_HUNDRED_OFFER_IDS +
                    SEVENTH_HUNDRED_OFFER_IDS)
        self.assertEqual(len(prior), 560)
        self.assertEqual(len(EIGHTH_TWO_HUNDRED_OFFER_IDS), 200)
        self.assertEqual(len(set(EIGHTH_TWO_HUNDRED_OFFER_IDS)), 200)
        self.assertFalse(prior.intersection(EIGHTH_TWO_HUNDRED_OFFER_IDS))
        self.assertEqual(len(prior | set(EIGHTH_TWO_HUNDRED_OFFER_IDS)), 760)

    def test_screenshot_actions_enabled_for_entire_batch(self):
        self.assertEqual(set(EIGHTH_TWO_HUNDRED_OFFER_IDS) -
                         set(COMPARISON_BATCH_OFFER_IDS), set())
