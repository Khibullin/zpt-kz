"""Load the November 18 Apify snapshot into verified supplier Kaspi quotes.

Only matching existing supplier offers are updated; stock and purchase prices
are untouched. All source rows remain archived in procurement/data.
"""
import base64
import io
import json
import zlib
from datetime import date
from pathlib import Path

from django.db import migrations


def load_apify_snapshot(apps, schema_editor):
    from procurement.apify_import import import_apify_kaspi
    from procurement.opportunities import owner_terms

    terms = owner_terms()
    if terms is None or terms.user is None:
        raise RuntimeError("Нет администратора с настроенными условиями закупки.")
    source = Path(__file__).resolve().parent.parent / "data" / "apify_kaspi_20261118.b64"
    encoded = "".join(source.read_text(encoding="utf-8").split())
    payload = zlib.decompress(base64.b64decode(encoded))
    rows = json.loads(payload)
    if len(rows) != 6144:
        raise RuntimeError("Исходная выгрузка Apify повреждена.")
    import_apify_kaspi(
        io.StringIO(json.dumps(rows, ensure_ascii=False)),
        "Все поставщики",
        date(2026, 11, 18),
        terms.user,
    )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("procurement", "0122_kaspi_buying_candidate")]
    operations = [migrations.RunPython(load_apify_snapshot, migrations.RunPython.noop)]
