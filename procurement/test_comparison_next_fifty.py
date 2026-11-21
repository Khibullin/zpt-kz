"""Safety regression checks for the approved second comparison batch."""
from django.test import SimpleTestCase
from django.urls import reverse, resolve

from .comparison_first_ten import FIRST_TEN_OFFER_IDS
from .comparison_next_fifty import NEXT_FIFTY_OFFER_IDS


class NextFiftyComparisonTests(SimpleTestCase):
    def test_batch_is_exactly_fifty_distinct_offers_after_first_ten(self):
        self.assertEqual(len(NEXT_FIFTY_OFFER_IDS), 50)
        self.assertEqual(len(set(NEXT_FIFTY_OFFER_IDS)), 50)
        self.assertFalse(set(NEXT_FIFTY_OFFER_IDS) & set(FIRST_TEN_OFFER_IDS))

    def test_review_page_and_approved_comparison_route(self):
        self.assertEqual(reverse('procurement:comparison_next_fifty'),
                         '/office/procurement/comparison/next-50/')
        self.assertEqual(resolve(reverse('procurement:comparison',
                                         args=[NEXT_FIFTY_OFFER_IDS[0]])).url_name,
                         'comparison')
