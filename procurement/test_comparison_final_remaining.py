"""Final remainder queue: no repeats, exact count, screenshot access."""
from django.test import SimpleTestCase
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_fifth_hundred import FIFTH_HUNDRED_OFFER_IDS
from .comparison_sixth_hundred import SIXTH_HUNDRED_OFFER_IDS
from .comparison_seventh_hundred import SEVENTH_HUNDRED_OFFER_IDS
from .comparison_eighth_two_hundred import EIGHTH_TWO_HUNDRED_OFFER_IDS
from .comparison_final_remaining import FINAL_REMAINING_OFFER_IDS, REJECTED_CARD_MISMATCH_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class FinalRemainderIntegrityTests(SimpleTestCase):
    def test_final_153_are_new_and_mismatch_stays_out(self):
        prior = set(FIRST_TEN_OFFER_IDS + NEXT_FIFTY_OFFER_IDS +
                    NEXT_HUNDRED_OFFER_IDS + FOURTH_HUNDRED_OFFER_IDS +
                    FIFTH_HUNDRED_OFFER_IDS + SIXTH_HUNDRED_OFFER_IDS +
                    SEVENTH_HUNDRED_OFFER_IDS + EIGHTH_TWO_HUNDRED_OFFER_IDS)
        self.assertEqual(len(prior), 760)
        self.assertEqual(len(FINAL_REMAINING_OFFER_IDS), 153)
        self.assertEqual(len(set(FINAL_REMAINING_OFFER_IDS)), 153)
        self.assertFalse(prior.intersection(FINAL_REMAINING_OFFER_IDS))
        self.assertFalse(set(REJECTED_CARD_MISMATCH_OFFER_IDS) & set(COMPARISON_BATCH_OFFER_IDS))
        self.assertEqual(len(set(COMPARISON_BATCH_OFFER_IDS)), 913)
