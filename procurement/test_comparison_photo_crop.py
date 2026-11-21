"""Regression checks for screenshot-to-Kaspi-photo crop safety."""
import base64
import io

from django.test import SimpleTestCase
from PIL import Image

from .comparison_photo_crop import crop_photo, normalize_box, PREFIX


class KaspiPhotoCropTests(SimpleTestCase):
    def screenshot(self):
        image = Image.new('RGB', (1000, 800), 'white')
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG')
        return PREFIX + base64.b64encode(buffer.getvalue()).decode('ascii')

    def test_crops_only_selected_rectangle(self):
        saved = self.screenshot()
        photo = crop_photo([saved], [0, 200, 150, 750, 700])
        self.assertTrue(photo.startswith(PREFIX))
        image = Image.open(io.BytesIO(base64.b64decode(photo[len(PREFIX):])))
        self.assertEqual(image.size, (550, 440))
        self.assertNotEqual(photo, saved)

    def test_rejects_unbounded_or_tiny_rectangles(self):
        saved = self.screenshot()
        self.assertIsNone(crop_photo([saved], [0, -1, 0, 1000, 1000]))
        self.assertIsNone(crop_photo([saved], [0, 100, 100, 170, 180]))
        self.assertIsNone(crop_photo([saved], [1, 0, 0, 500, 500]))
        self.assertIsNone(crop_photo([saved], [0, 100, 100, 900, 2000]))
        self.assertIsNone(normalize_box(None, 1))
