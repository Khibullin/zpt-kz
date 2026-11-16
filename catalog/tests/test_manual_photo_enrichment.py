"""Regression checks for owner-triggered supplier photo enrichment."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class ManualPhotoEnrichmentTests(SimpleTestCase):
    @override_settings(CATALOG_PHOTO_AUTORUN_ENABLED=False)
    def test_autorun_off_by_default(self):
        from django.conf import settings
        self.assertFalse(settings.CATALOG_PHOTO_AUTORUN_ENABLED)

    @patch('catalog.management.commands.run_manual_photo_enrichment.SupplierPhotoTask')
    @patch('catalog.management.commands.run_manual_photo_enrichment.process_photo_tasks')
    def test_without_run_never_processes(self, processor, tasks):
        tasks.objects.filter.return_value.count.return_value = 3
        out = StringIO()
        call_command('run_manual_photo_enrichment', '--limit', '10', stdout=out)
        processor.assert_not_called()
        self.assertIn('DRY RUN', out.getvalue())

    @patch('catalog.management.commands.run_manual_photo_enrichment.SupplierPhotoTask')
    @patch('catalog.management.commands.run_manual_photo_enrichment.process_photo_tasks')
    def test_explicit_run_uses_bounded_limit(self, processor, tasks):
        processor.return_value = 2
        out = StringIO()
        call_command('run_manual_photo_enrichment', '--run', '--limit', '10', stdout=out)
        processor.assert_called_once_with(limit=10)
        self.assertIn('processed=2', out.getvalue())

    def test_invalid_limit_rejected(self):
        with self.assertRaises(CommandError):
            call_command('run_manual_photo_enrichment', '--run', '--limit', '101')
