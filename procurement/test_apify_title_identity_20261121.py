"""Regressions for exact Kaspi/Omega identity extraction from card titles.

A textual match is never enough for final purchase approval.
"""
from django.test import SimpleTestCase
from .eep_comparison import title_mentions_identifier


class KaspiTitleIdentityTests(SimpleTestCase):
    def test_exact_brand_article_and_multipack_tokens(self):
        title = "STINSS передний S2S1552 1 шт"
        self.assertTrue(title_mentions_identifier(title, "stinss"))
        self.assertTrue(title_mentions_identifier(title, "S2S1552"))
        self.assertFalse(title_mentions_identifier(title, "S2S155"))

    def test_hyphen_and_multiword_brand(self):
        title = "Амортизатор SRR OE SA-160004 1 шт"
        self.assertTrue(title_mentions_identifier(title, "SRR OE"))
        self.assertTrue(title_mentions_identifier(title, "SA-160004"))
        self.assertFalse(title_mentions_identifier(title, "SA-1600040"))

    def test_brand_edition_mismatch_stays_distinct(self):
        self.assertFalse(title_mentions_identifier("SRR SA-160004 1 шт", "SRR OE"))
        self.assertFalse(title_mentions_identifier("SRR SA-160004 1 шт", "SRR/JAPAN"))
        self.assertFalse(title_mentions_identifier("SRR612460", "SRR61246"))

    def test_no_inferred_identity_from_absent_data(self):
        self.assertFalse(title_mentions_identifier("", "SRR"))
        self.assertFalse(title_mentions_identifier("SRR OE", ""))
        self.assertFalse(title_mentions_identifier(None, "SRR"))
