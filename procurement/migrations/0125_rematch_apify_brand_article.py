"""Recompute 6144 Kaspi buying matches using exact brand and article only."""
import base64
import json
import zlib
from pathlib import Path
from django.db import migrations


def rematch_candidates(apps, schema_editor):
    from django.core.files.base import ContentFile
    from procurement.apify_buying_import import import_apify_results
    source = Path(__file__).resolve().parent.parent / "data" / "apify_kaspi_20261118.b64"
    decoded = zlib.decompress(base64.b64decode("".join(source.read_text(encoding="utf-8").split())))
    rows = json.loads(decoded)
    if len(rows) != 6144:
        raise RuntimeError("Ожидалось 6144 карточки Apify.")
    for row in rows:
        if str(row.get("cityId")) == "750000000":
            row["city"] = "Алматы"
    raw = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    import_apify_results(ContentFile(raw, name="apify_20261118.json"), "apify_20261118.json")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("procurement", "0124_archive_apify_candidates_20261118")]
    operations = [migrations.RunPython(rematch_candidates, migrations.RunPython.noop)]
