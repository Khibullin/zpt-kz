import tempfile
from io import BytesIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.maintenance_kit_covers import (
    EXACT_COVER_FILES,
    pick_cover_image,
    plan_cover_attach,
)
from catalog.maintenance_kit_seed import apply_maintenance_kits
from catalog.models import Brand, CarModel, Country, MaintenanceKit, Product
from catalog.tests.test_maintenance_kits import _make_product


def _png_bytes(color, size=(120, 80)):
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def _write_png(path: Path, color, size=(120, 80)):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes(color, size))
    return path


class MaintenanceKitCoverAttachTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай COVER')
        chery = Brand.objects.create(country=country, name='Chery')
        exeed = Brand.objects.create(country=country, name='Exeed')
        changan = Brand.objects.create(country=country, name='Changan')
        CarModel.objects.create(brand=chery, name='Tiggo 7 Pro')
        CarModel.objects.create(brand=exeed, name='TXL')
        CarModel.objects.create(brand=changan, name='UNI-K')
        articles = {
            'T151109111': 'Воздушный фильтр Chery',
            'T218107011': 'Салонный фильтр Chery',
            '4801012010': 'Масляный фильтр Chery',
            'F4J163707010': 'Свеча Chery',
            '151000025AA': 'Воздушный фильтр Exeed',
            '301001199AA': 'Салонный фильтр Exeed',
            'F4J161012030': 'Масляный фильтр Exeed',
            '1109190CR01': 'Воздушный фильтр UNI-K',
            'CD569F2801032700': 'Салонный фильтр UNI-K',
            'D20T0120700': 'Свеча UNI-K',
        }
        for article, title in articles.items():
            _make_product(article=article, title=title, stock_qty=5)
        apply_maintenance_kits()

    def test_picker_skips_part_photos_for_general_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            part = _write_png(folder / 'F4J161012030.png', (10, 10, 10), (40, 40))
            cover = _write_png(folder / 'общий-набор.png', (200, 30, 30), (400, 300))
            _write_png(folder / 'масляный-фильтр.png', (20, 20, 20), (80, 80))
            chosen, reason = pick_cover_image(folder)
            self.assertEqual(chosen, cover)
            self.assertEqual(reason, '')
            self.assertNotEqual(chosen, part)

    def test_picker_prefers_verified_cover_filename(self):
        slug = 'komplekt-to-exeed-txl-16t'
        exact_name = EXACT_COVER_FILES[slug]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            part = _write_png(folder / 'F4J161012030.png', (10, 10, 10), (400, 400))
            other = _write_png(folder / 'общий.png', (200, 30, 30), (800, 600))
            cover = _write_png(folder / exact_name, (30, 120, 200), (1600, 900))
            chosen, reason = pick_cover_image(folder, slug=slug)
            self.assertEqual(chosen, cover)
            self.assertEqual(reason, '')
            self.assertNotEqual(chosen, part)
            self.assertNotEqual(chosen, other)

    def test_command_attaches_and_replaces_without_new_kits(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'media'
            source = Path(tmp) / 'source'
            media.mkdir()
            _write_png(source / 'Комплект 1' / 'общий.png', (180, 40, 40), (320, 240))
            _write_png(source / 'Комплект 1' / 'F4J161012030.png', (10, 10, 10), (40, 40))
            _write_png(source / 'Комплект 2' / 'cover.png', (40, 180, 40), (320, 240))
            _write_png(source / 'Комплект 3' / 'общий.png', (40, 40, 180), (320, 240))
            with override_settings(MEDIA_ROOT=media):
                plans = plan_cover_attach(source)
                self.assertTrue(all(plan.can_apply for plan in plans[:3]))
                call_command(
                    'attach_maintenance_kit_covers',
                    f'--source={source}',
                )
                exeed = MaintenanceKit.objects.get(slug='komplekt-to-exeed-txl-16t')
                self.assertFalse(exeed.cover)
                call_command(
                    'attach_maintenance_kit_covers',
                    f'--source={source}',
                    '--apply',
                )
                self.assertEqual(MaintenanceKit.objects.count(), 3)
                exeed.refresh_from_db()
                chery = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
                changan = MaintenanceKit.objects.get(slug='komplekt-to-changan-uni-k-20t')
                self.assertTrue(exeed.cover)
                self.assertTrue(chery.cover)
                self.assertTrue(changan.cover)
                self.assertFalse(changan.is_active)
                call_command(
                    'attach_maintenance_kit_covers',
                    f'--source={source}',
                    '--apply',
                )
                exeed.refresh_from_db()
                self.assertTrue(exeed.cover)
                listing = self.client.get(reverse('maintenance_kit_list'))
                self.assertContains(listing, 'kit-card-cover')
                self.assertContains(listing, exeed.cover.url)
                detail = self.client.get('/maintenance-kits/komplekt-to-exeed-txl-16t/')
                self.assertContains(detail, 'kit-cover-wrap')
                self.assertContains(detail, exeed.cover.url)
                hidden = self.client.get(
                    '/maintenance-kits/komplekt-to-changan-uni-k-20t/'
                )
                self.assertEqual(hidden.status_code, 404)
                self.assertEqual(exeed.cover.name.split('/')[0], 'maintenance_kits')

    def test_cover_field_can_be_set_from_upload_bytes(self):
        kit = MaintenanceKit.objects.get(slug='komplekt-to-exeed-txl-16t')
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                kit.cover.save(
                    'manual.png',
                    SimpleUploadedFile('manual.png', _png_bytes((90, 90, 20))),
                    save=True,
                )
                kit.refresh_from_db()
                self.assertTrue(kit.cover.name.startswith('maintenance_kits/'))
                response = self.client.get(reverse('maintenance_kit_list'))
                self.assertContains(response, kit.cover.url)
                self.assertContains(response, 'maintenance-kits.css')
