"""Third batch integrity: exactly one hundred candidates, none repeated."""
from django.test import SimpleTestCase
from django.urls import reverse

from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS
from .comparison_next_hundred import NEXT_HUNDRED_OFFER_IDS
from .comparison_clipboard import COMPARISON_BATCH_OFFER_IDS


class ThirdBatchIntegrityTests(SimpleTestCase):
    def test_one_hundred_unique_after_first_sixty(self):
        self.assertEqual(len(NEXT_HUNDRED_OFFER_IDS), 100)
        self.assertEqual(len(set(NEXT_HUNDRED_OFFER_IDS)), 100)
        self.assertFalse(set(NEXT_HUNDRED_OFFER_IDS) &
                         (set(FIRST_TEN_OFFER_IDS) | set(NEXT_FIFTY_OFFER_IDS)))

    def test_clipboard_scope_covers_all_three_batches(self):
        self.assertEqual(set(COMPARISON_BATCH_OFFER_IDS),
                         set(FIRST_TEN_OFFER_IDS) | set(NEXT_FIFTY_OFFER_IDS) |
                         set(NEXT_HUNDRED_OFFER_IDS))

    def test_main_list_url(self):
        self.assertEqual(reverse('procurement:dashboard'),
                         '/office/procurement/')
