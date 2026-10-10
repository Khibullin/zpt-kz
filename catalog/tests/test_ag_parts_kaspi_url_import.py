from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class KaspiLinkImportSafetyTests(SimpleTestCase):
    def test_invalid_external_url_is_rejected_before_database_lookup(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.csv"
            path.write_text(
                "master_sku,public_url\n123456,https://not-kaspi.example/shop/p/item-123456/\n",
                encoding="utf-8",
            )
            with self.assertRaises(CommandError):
                call_command("import_ag_parts_kaspi_links", path, apply=True)

    def test_csv_requires_both_columns(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.csv"
            path.write_text("sku,url\n123456,abc\n", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("import_ag_parts_kaspi_links", path)
