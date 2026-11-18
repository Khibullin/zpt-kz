"""Archive all 6144 original Kaspi cards as buying candidates, including unmatched."""
import base64
import json
import zlib
from pathlib import Path
from django.db import migrations


def archive_candidates(apps, schema_editor):
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
    dependencies = [("procurement", "0123_import_apify_kaspi_20261118")]
    operations = [migrations.RunPython(archive_candidates, migrations.RunPython.noop)]
