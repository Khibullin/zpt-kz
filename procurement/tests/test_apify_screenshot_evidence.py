"""Regression checks for source-scoped screenshot evidence lifecycle."""
from django.test import SimpleTestCase
from procurement.comparison_clipboard import append_screenshot_evidence


class ApifyScreenshotEvidenceTests(SimpleTestCase):
    FIRST = 'data:image/jpeg;base64,QUJD'
    SECOND = 'data:image/jpeg;base64,REVG'
    THIRD = 'data:image/jpeg;base64,R0hJ'
    FOURTH = 'data:image/jpeg;base64,SktM'

    def test_identical_paste_is_idempotent_and_does_not_revoke_review(self):
        existing = {
            'manual_screenshots': [self.FIRST],
            'manual_confirmed_at': '2026-11-21T20:00:00+05:00',
            'manual_confirmed_by': 42,
            'images': ['https://example.com/photo.jpg'],
        }
        updated, changed = append_screenshot_evidence(existing, self.FIRST)
        self.assertFalse(changed)
        self.assertIs(updated, existing)
        self.assertEqual(updated['manual_confirmed_by'], 42)

    def test_new_screenshot_revokes_confirmation_and_derived_crop(self):
        existing = {
            'manual_screenshots': [self.FIRST],
            'manual_confirmed_at': '2026-11-21T20:00:00+05:00',
            'manual_confirmed_by': 42,
            'photo_origin': 'cropped_from_manual_screenshot',
            'images': ['data:image/jpeg;base64,Q1JPUA=='],
            'title': 'historical raw Apify title',
        }
        updated, changed = append_screenshot_evidence(existing, self.SECOND)
        self.assertTrue(changed)
        self.assertEqual(updated['manual_screenshots'], [self.FIRST, self.SECOND])
        self.assertNotIn('manual_confirmed_at', updated)
        self.assertNotIn('manual_confirmed_by', updated)
        self.assertNotIn('photo_origin', updated)
        self.assertNotIn('images', updated)
        self.assertEqual(updated['title'], existing['title'])
        self.assertIn('manual_confirmed_at', existing)  # input stays untouched

    def test_enforces_three_screenshot_limit_without_mutation(self):
        existing = {'manual_screenshots': [self.FIRST, self.SECOND, self.THIRD]}
        with self.assertRaises(ValueError):
            append_screenshot_evidence(existing, self.FOURTH)
        self.assertEqual(len(existing['manual_screenshots']), 3)
