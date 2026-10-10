from django.test import SimpleTestCase
from catalog.management.commands.export_kaspi_id_candidates import candidate_public_id


class CandidateIdTests(SimpleTestCase):
    def test_composite_sku(self):
        self.assertEqual(candidate_public_id("119186248_871011130"), "119186248")

    def test_single_sku_not_assumed_public_id(self):
        self.assertEqual(candidate_public_id("423766246"), "")

    def test_non_numeric_sku(self):
        self.assertEqual(candidate_public_id("X01-90000014"), "")

    def test_second_part_not_numeric(self):
        self.assertEqual(candidate_public_id("119186248_test"), "")
