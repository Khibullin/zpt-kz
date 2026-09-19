"""Unit tests for short-form query splitting and article detection."""
from django.test import SimpleTestCase

from core.services.home_parts_query import (
    looks_like_exact_article,
    query_requires_vehicle,
    split_part_queries,
)


class HomePartsQueryTests(SimpleTestCase):
    def test_split_comma_separated_parts(self):
        self.assertEqual(
            split_part_queries('колодки, масляный фильтр, свечи'),
            ['колодки', 'масляный фильтр', 'свечи'],
        )

    def test_keeps_decimal_comma_in_engine_size(self):
        self.assertEqual(split_part_queries('мотор 1,6'), ['мотор 1,6'])
        self.assertEqual(
            split_part_queries('2,0 TDI, фильтр'),
            ['2,0 TDI', 'фильтр'],
        )

    def test_splits_semicolons_and_newlines(self):
        self.assertEqual(
            split_part_queries('колодки; фильтр\nсвечи'),
            ['колодки', 'фильтр', 'свечи'],
        )

    def test_name_with_digit_is_not_exact_article(self):
        self.assertFalse(looks_like_exact_article('фильтр H75'))
        self.assertTrue(query_requires_vehicle('фильтр H75'))
        self.assertFalse(looks_like_exact_article('filter H75'))

    def test_oem_code_is_exact_article(self):
        self.assertTrue(looks_like_exact_article('52119-0K040'))
        self.assertFalse(query_requires_vehicle('52119-0K040'))
        self.assertTrue(looks_like_exact_article('W712/75'))
