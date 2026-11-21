"""Fourth profitable buying batch: unique offer IDs and approved detail workflow."""
from django.test import SimpleTestCase
from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_fourth_hundred import FOURTH_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class FourthBatchIntegrityTests(SimpleTestCase):
    def test_one_hundred_unique_without_previous_batches(self):
        self.assertEqual(len(FOURTH_HUNDRED_OFFER_IDS), 100)
        self.assertEqual(len(set(FOURTH_HUNDRED_OFFER_IDS)), 100)
        old = set(FIRST_TEN_OFFER_IDS) | set(NEXT_FIFTY_OFFER_IDS) | set(NEXT_HUNDRED_OFFER_IDS)
        self.assertFalse(old & set(FOURTH_HUNDRED_OFFER_IDS))

    def test_clipboard_and_comparison_scope(self):
        expected = set(FIRST_TEN_OFFER_IDS) | set(NEXT_FIFTY_OFFER_IDS) | set(NEXT_HUNDRED_OFFER_IDS) | set(FOURTH_HUNDRED_OFFER_IDS)
        self.assertEqual(set(COMPARISON_BATCH_OFFER_IDS), expected)
        self.assertEqual(len(expected), 260)
