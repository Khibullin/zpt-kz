"""Browser layout tests of approved ZPT.KZ desktop and mobile UI.

Runs only on the isolated CI database. Screenshots are test artifacts, never
substitutes for or sources of production product / organization images.
"""
from pathlib import Path
from unittest import skipIf

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@skipIf(sync_playwright is None, "Playwright is only installed in UI CI")
class LandingBrowserSmokeTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.screenshots = Path("artifacts/ui-layout")
        cls.screenshots.mkdir(parents=True, exist_ok=True)

    def test_desktop_mobile_pages_do_not_overflow_viewport(self):
        from catalog.models import Product
        from service_requests.models import ServiceSeller

        # Sample records exist only inside the temporary test database.
        Product.objects.create(
            title="Фильтр масляный QA", article="QA-FILTER-1",
            price=3200, status="active", seller_name="QA seller",
            whatsapp_number="+77001234567",
        )
        ServiceSeller.objects.create(
            name="QA СТО", whatsapp="+77001234568", city="Алматы",
            seller_type="sto", is_active=True,
        )
        scenarios = [
            ("home", "/", ".tiles-five .tile", 5),
            ("consumables", "/to-i-rashodniki/", ".landing-choices .landing-choice", 3),
            ("services", "/sto-i-uslugi/", ".landing-choices .landing-choice", 2),
        ]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chrome", headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                for device, width, height in (
                    ("desktop", 1440, 900),
                    ("mobile", 390, 844),
                ):
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                    )
                    for label, path, selector, count in scenarios:
                        with self.subTest(device=device, page=label):
                            response = page.goto(
                                self.live_server_url + path,
                                wait_until="domcontentloaded", timeout=20000,
                            )
                            self.assertEqual(response.status, 200)
                            page.locator(selector).first.wait_for()
                            self.assertEqual(page.locator(selector).count(), count)
                            dimensions = page.evaluate(
                                """() => ({
                                    viewport: document.documentElement.clientWidth,
                                    content: document.documentElement.scrollWidth
                                })"""
                            )
                            self.assertLessEqual(
                                dimensions["content"],
                                dimensions["viewport"] + 2,
                                f"{device}/{label}: horizontal overflow {dimensions}",
                            )
                            self.assertEqual(
                                page.locator(".zpt-page-navigation").evaluate(
                                    "(el) => getComputedStyle(el).display"
                                ) if page.locator(".zpt-page-navigation").count() else "none",
                                "none",
                            )
                            if device == "desktop":
                                self.assertNotEqual(
                                    page.locator(".footer-desktop").evaluate(
                                        "(el) => getComputedStyle(el).position"
                                    ),
                                    "fixed",
                                )
                            else:
                                self.assertIn(
                                    page.locator(".header-cart").evaluate(
                                        "(el) => getComputedStyle(el).display"
                                    ),
                                    ("flex", "inline-flex"),
                                )
                            if label == "home":
                                tiles = page.locator(selector)
                                first = tiles.nth(0).bounding_box()
                                second = tiles.nth(1).bounding_box()
                                fifth = tiles.nth(4).bounding_box()
                                self.assertAlmostEqual(first["y"], second["y"], delta=3)
                                self.assertGreater(second["x"], first["x"])
                                self.assertGreater(fifth["width"], first["width"] * 1.6)
                                self.assertEqual(page.locator(".hero .btn").count(), 2)
                                self.assertTrue(page.locator('[data-zpt-entry="buyer"]').is_visible())
                                self.assertTrue(page.locator('[data-zpt-entry="seller"]').is_visible())
                                self.assertIn('?request=1', page.locator('[data-zpt-entry="buyer"]').get_attribute('href'))
                            page.screenshot(
                                path=str(self.screenshots / f"{label}-{device}.png"),
                                full_page=True, animations="disabled",
                            )
                    page.close()
            finally:
                browser.close()
