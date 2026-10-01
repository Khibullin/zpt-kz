from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.maintenance_kit_collages import (
    KIT_COLLAGE_STATIC,
    kit_collage_static_path,
    kit_cover_image,
)
from catalog.maintenance_kit_seed import apply_maintenance_kits
from catalog.maintenance_kits import build_kit_view
from catalog.models import Brand, CarModel, Country, MaintenanceKit
from catalog.tests.test_maintenance_kit_covers import _png_bytes
from catalog.tests.test_maintenance_kits import _make_product


class MaintenanceKitCollageCoverTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай COLLAGE')
        chery = Brand.objects.create(country=country, name='Chery')
        exeed = Brand.objects.create(country=country, name='Exeed')
        changan = Brand.objects.create(country=country, name='Changan')
        haval = Brand.objects.create(country=country, name='Haval')
        great_wall = Brand.objects.create(country=country, name='Great Wall')
        li_auto = Brand.objects.create(country=country, name='Li Auto')
        CarModel.objects.create(brand=chery, name='Tiggo 7 Pro')
        CarModel.objects.create(brand=chery, name='Tiggo 7')
        CarModel.objects.create(brand=chery, name='Tiggo 8 Pro')
        CarModel.objects.create(brand=chery, name='Tiggo 8')
        CarModel.objects.create(brand=chery, name='Arrizo 8')
        CarModel.objects.create(brand=chery, name='Tiggo 2')
        CarModel.objects.create(brand=chery, name='M11')
        CarModel.objects.create(brand=exeed, name='TXL')
        CarModel.objects.create(brand=exeed, name='VX')
        CarModel.objects.create(brand=changan, name='UNI-K')
        CarModel.objects.create(brand=changan, name='UNI-V')
        CarModel.objects.create(brand=haval, name='Dargo')
        CarModel.objects.create(brand=haval, name='H9')
        CarModel.objects.create(brand=great_wall, name='Wingle 7')
        CarModel.objects.create(brand=great_wall, name='Poer')
        CarModel.objects.create(brand=li_auto, name='L7')
        articles = {
            '1109110XKV08A': 'Воздушный Haval H9',
            '8100103XKV08A': 'Салонный Haval H9',
            '1017110XED95': 'Масляный Poer дизель',
            '8100422XNZ01A': 'Салонный Poer',
            '151000187AA': 'Воздушный TXL VX 2.0',
            '301000265AA': 'Салонный TXL VX 2.0',
            'M111109111': 'Воздушный Chery M11',
            'M118107915': 'Салонный Chery M11',
            'T151109111': 'Воздушный Chery',
            'T218107011': 'Салонный Chery',
            '4801012010': 'Масляный Chery',
            'F4J163707010': 'Свеча Chery',
            '151000025AA': 'Воздушный Exeed',
            '301001199AA': 'Салонный Exeed',
            'F4J161012030': 'Масляный Exeed',
            '151000079AA': 'Воздушный Chery 8 Pro',
            '1109190CR01': 'Воздушный UNI-K',
            'S3010140903': 'Воздушный UNI-V',
            'C281F2801032601': 'Салонный UNI-V',
            '1109101XGW01A': 'Воздушный Dargo',
            '1017110XEN01': 'Масляный Dargo',
            'J691109111': 'Воздушный Tiggo 2',
            'A138107915': 'Салонный Tiggo 2',
            '1109110XP64XA': 'Воздушный Wingle 7',
            '8104400XP24BA': 'Салонный Wingle 7',
            'X01-90000014': 'Воздушный Li Auto L7',
            'X0390000206': 'Салонный Li Auto L7',
        }
        for article, title in articles.items():
            _make_product(article=article, title=title, stock_qty=5)
        apply_maintenance_kits()

    def test_static_collage_files_are_valid_jpegs(self):
        static_root = Path(settings.BASE_DIR) / 'static'
        seen = set()
        for slug, relative in KIT_COLLAGE_STATIC.items():
            path = static_root / relative
            self.assertTrue(path.is_file(), msg=slug)
            self.assertGreater(path.stat().st_size, 1000, msg=slug)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self.assertEqual(image.format, 'JPEG')
                self.assertGreaterEqual(image.size[0], 800)
                self.assertGreaterEqual(image.size[1], 800)
            seen.add(path.name)
        self.assertEqual(len(seen), len(KIT_COLLAGE_STATIC))

    def test_unpublished_uni_k_has_no_collage_mapping(self):
        self.assertEqual(kit_collage_static_path('komplekt-to-changan-uni-k-20t'), '')

    def test_templates_do_not_special_case_slug(self):
        templates = (
            Path(settings.BASE_DIR) / 'catalog/templates/catalog/maintenance_kit_list.html',
            Path(settings.BASE_DIR) / 'catalog/templates/catalog/maintenance_kit_detail.html',
        )
        for path in templates:
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('kit.slug ==', text)
            self.assertNotIn('kit_view.kit.slug ==', text)

    def test_list_and_detail_share_the_same_collage_url(self):
        listing = self.client.get(reverse('maintenance_kit_list'))
        html = listing.content.decode('utf-8')
        for slug, relative in KIT_COLLAGE_STATIC.items():
            kit = MaintenanceKit.objects.get(slug=slug)
            url, alt = kit_cover_image(kit)
            self.assertIn(relative, url)
            self.assertIn(url, html)
            detail = self.client.get(f'/maintenance-kits/{slug}/')
            self.assertEqual(detail.status_code, 200)
            self.assertContains(detail, url)
            self.assertContains(detail, alt)
            view = detail.context['kit_view']
            self.assertEqual(view.cover_image_url, url)
            list_view = next(
                item for item in listing.context['kits'] if item.kit.slug == slug
            )
            self.assertEqual(list_view.cover_image_url, view.cover_image_url)

    def test_collage_wins_over_leftover_uploaded_cover(self):
        kit = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
        with override_settings(MEDIA_ROOT=self._tmp_media()):
            kit.cover.save(
                'old-cover.png',
                SimpleUploadedFile('old-cover.png', _png_bytes((20, 20, 20))),
                save=True,
            )
            kit.refresh_from_db()
            url, _alt = kit_cover_image(kit)
            self.assertIn('tiggo-7-pro-15t-three-filter-collage.jpg', url)
            self.assertNotIn(kit.cover.url, url)
            listing = self.client.get(reverse('maintenance_kit_list'))
            self.assertContains(listing, url)
            self.assertNotContains(listing, kit.cover.url)

    def test_unmapped_kit_still_uses_uploaded_cover(self):
        brand = Brand.objects.get(name='Chery')
        model = CarModel.objects.get(brand=brand, name='Arrizo 8')
        kit = MaintenanceKit.objects.create(
            name='Тестовый набор без коллажа',
            slug='kit-no-collage-cover',
            brand=brand,
            car_model=model,
            engine='test',
            is_active=True,
        )
        product = _make_product(article='NO-COLLAGE-1', title='Фильтр', stock_qty=3)
        kit.items.create(product=product, quantity=1)
        with override_settings(MEDIA_ROOT=self._tmp_media()):
            kit.cover.save(
                'plain.png',
                SimpleUploadedFile('plain.png', _png_bytes((90, 10, 10))),
                save=True,
            )
            kit.refresh_from_db()
            view = build_kit_view(kit)
            self.assertEqual(view.cover_image_url, kit.cover.url)
            listing = self.client.get(reverse('maintenance_kit_list'))
            self.assertContains(listing, kit.cover.url)

    def _tmp_media(self):
        import tempfile
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        return self._media.name
