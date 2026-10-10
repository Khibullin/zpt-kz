from django.test import SimpleTestCase
from django.urls import resolve, reverse

from catalog.views_ag_parts import ag_parts_store


class AgPartsStoreRoutingTests(SimpleTestCase):
    def test_own_products_url(self):
        self.assertEqual(reverse("ag_parts_store"), "/our-products/")

    def test_own_products_view(self):
        self.assertIs(resolve("/our-products/").func, ag_parts_store)
