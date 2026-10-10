from types import SimpleNamespace

from django.test import SimpleTestCase

from catalog.templatetags.kaspi_buy import kaspi_buy_url


class FakeListings:
    def __init__(self, *items):
        self.items = items

    def all(self):
        return self.items


def product_with_listings(*listings, published=True):
    return SimpleNamespace(
        publish_to_kaspi=published,
        kaspi_listings=FakeListings(*listings),
    )


def listing(url, active=True, published=True):
    return SimpleNamespace(
        public_url=url,
        is_active=active,
        publish_to_kaspi=published,
    )


class KaspiBuyUrlTests(SimpleTestCase):
    URL = "https://kaspi.kz/shop/p/test-product-12345678/"

    def test_verified_published_link(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(listing(self.URL))),
            self.URL,
        )

    def test_does_not_publish_disabled_product(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(listing(self.URL), published=False)),
            "",
        )

    def test_skips_unpublished_listing(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(listing(self.URL, published=False))),
            "",
        )

    def test_skips_inactive_listing(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(listing(self.URL, active=False))),
            "",
        )

    def test_never_uses_untrusted_domain(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(listing("https://evil.example/shop/p/fake-12345678/"))),
            "",
        )

    def test_uses_next_valid_listing(self):
        self.assertEqual(
            kaspi_buy_url(product_with_listings(
                listing("", active=True),
                listing(self.URL),
            )),
            self.URL,
        )

    def test_never_constructs_link_from_sku(self):
        self.assertEqual(kaspi_buy_url(product_with_listings()), "")
