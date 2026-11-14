from django.test import SimpleTestCase

from catalog.reviewed_weekend_20261114_f import display_engine


class ReviewedWeekend20261114FTests(SimpleTestCase):
    def test_exact_modification_and_engine_code_are_both_visible(self):
        self.assertEqual(display_engine({"modification": "2.9", "engine_code": "UMZ-4218"}), "2.9 · UMZ-4218")
        self.assertEqual(display_engine({"modification": "2.2 D", "engine_code": ""}), "2.2 D")
