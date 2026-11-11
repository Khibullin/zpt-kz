"""Integrity and identity checks for the bounded owner-reviewed photo batch."""
import hashlib
import io
from types import SimpleNamespace
from django.test import SimpleTestCase
from PIL import Image
from catalog.reviewed_top50 import reviewed_rows, exact_offer, photo_bytes


class ReviewedTop50Tests(SimpleTestCase):
    def test_batch_has_unique_exact_targets_and_real_bounded_images(self):
        rows = reviewed_rows()
        self.assertEqual(len(rows), 38)
        self.assertEqual(len({r['offer_id'] for r in rows}), 38)
        self.assertEqual(sum(bool(r.get('main_b64')) for r in rows), 36)
        self.assertEqual(sum(bool(r.get('fitments')) for r in rows), 12)
        for row in rows:
            if not row.get('main_b64'):
                continue
            for key, edge in [('main', 800), ('thumbnail', 180)]:
                with Image.open(io.BytesIO(photo_bytes(row, key))) as picture:
                    self.assertEqual(picture.format, 'WEBP')
                    self.assertLessEqual(max(picture.size), edge)

    def test_wrong_supplier_article_brand_or_id_is_rejected(self):
        row = reviewed_rows()[0]
        values = dict(pk=row['offer_id'], supplier_id=3, brand=row['brand'], article=row['article'])
        self.assertTrue(exact_offer(SimpleNamespace(**values), row))
        for key, value in [('pk', 1), ('supplier_id', 4), ('brand', 'OTHER'), ('article', row['article']+'X')]:
            self.assertFalse(exact_offer(SimpleNamespace(**{**values, key: value}), row))
        self.assertFalse(exact_offer(None, row))

    def test_corrupted_asset_is_rejected_before_storage(self):
        row = dict(reviewed_rows()[0])
        row['main_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            photo_bytes(row, 'main')
