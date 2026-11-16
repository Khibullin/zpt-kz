"""Import the first China purchase list as a historical, unreceived purchase.

Source: Первый закуп с Китая(3).xlsx (uploaded 16 November 2026).
The spreadsheet gives no supplier legal name, purchase date, or warehouse acceptance.
Never create a StockDocument or StockEntry from this migration.
"""
import base64
import json
import uuid
import zlib
from decimal import Decimal
from django.conf import settings
from django.db import migrations

DATA = "eNrNml9Po0UUxr9K0ytN4M35OzNn70ppl38LTQtuF2MMsmskQTayeGGMN3rjl/A7eGNMTPwO8I088wK6QbTT7pD2Ai76FvrjzJnnec4Mn37fPbm8Ojs9f9N91t0kOAQkiADdte4XlycXr/3V61+uf+vc/HT9x/Wv17/715/+7OLk6/wDk1FvvNsZ7R0973wknZufb3782B9+8+3JxdXZ1XfdZwgAa93Tt++u/M1IlBqNMVmUFIUj+Xu/evf56dvX+XclRczvB+v+sPY+1VB2MHB0KlwEaxYVY2owYARSJA1cBnWI/gTMn5Yh9c4uO1+enV+9ufxvFIPQRLYHZRFyxIzwgECS18MXq7QoB2fnMwmSEximfwEQPwIwBVy3/DqgFBZhe9wZbu8dDsYPEf4mCNEaZC8CJYqoUFiMKXCLQhDKUPq97Y3t/Vk0CDE0wMDRS0CBopTi3JdGrS4PY2PsVJpUSNruK+BJyXtLzFhq0ljAxivDFklS8gKVwYyHXpXj3XFNFDXwukhsxYtj1DIUYmGxwGEFWFxQ8qvRer1quykyNaAsYpTQPzeWoewEm0/cZpNoogZRooGZuL8U9soLxOok5ApjSkFUwJc+FC5PxgCZPn8JVHGBiBtiVlc7UOWg88BghsF6MCHFxpSTuJZm+ZU5YPBguvsJpHowJuxmiKoKnhdCKlymCWZXFJDSCFPSMapNcA6KMcQQEs9RF4P+GHDZ/TLh26oYcD0UsSYYk6Gw56biqtzKHGlFmfsgxfXvFVGCeYaLHN1tY1KQNBdKihVRUvKsYOQZRkOMVFwVTwotS70N5OG9EVdaT9MxiagWKgsBE2S3qIcS1WcQZI+0ColECx0xBK8IoytkPRLQRo1d2UAFE+E8agvTUZDp0oPCPzCDaa+/NanXu4jeMRQ5WfDhw+axIoajvJ2qsYixD0WqCYijuS3Oo/8CL7Vm/y5oiy0LwVGKNQuD0rgHkbkVuf7jvKtENU16we00hHQXMLc2l80y2uhnllBxiTy0eLt4s/gncgAJhb077A3asoSKJqCOYsHTpQepHF8LUY53Rv1X2s7SWjqmFUxpi+7qQ8KE+SwKa8KE2LgtJsxnXMm0cFf3kDOL/xkVWRijO2RETT7W+++34kmtPot43GUNhBg8Y0Lp0ZiDiGtLb3JcPJGU4Xh4CKy+QObdjKWR9w5nuvuK0sZK4HiX8Tzz2lPiTFx7h5QPM73zSg3hKXdVf1OD3RFRXaIFzx76HsrugQJUFR7xycncpjgmToFTeUcj0IiLT5/LpMeHJw98IkncOGPpAUSmcD224pOzpyxNCwMUtC7MglHLe8a9v3IXL6rKPvTr7UXCqrBoXZYFI8W9P4xINmo2DafU5KsCH6PIEgCW+4MQTfePiw8XnxaHPBH76OIzHekq8PhQh5mHqmrfgvH4vjx55F0vv/16uvoMMbUBWXAF9I8iuY28kLr7fEGYUWubAstfIwQP+AjTwX5pmjjY3usMjwZ7H3DB8+gl8taRgyhOK1EULM2jGO3/GLTX6Qy1UGbG4UdJEDQA5Ue1Fmbm7cX/cKjV4zBrokpgRsnnIlbKcdeqm6ZLrggDc7K0vhJiMnhBg/V8VuvRZ9CsANCk9x7Q+mBJ6v/ZXxg5ouc="
SOURCE_SHA256 = "e03cf42556bc1d5ba57c9756bde19c44c664d96830bc4de465f2c2af24f1a325"
REQUEST_KEY = uuid.uuid5(uuid.NAMESPACE_URL, "zpt.kz/china/first-purchase/" + SOURCE_SHA256)


def import_first_china_purchase(apps, schema_editor):
    PurchaseOrder = apps.get_model("procurement", "PurchaseOrder")
    Supplier = apps.get_model("procurement", "Supplier")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    user = User.objects.filter(username="admin", is_superuser=True, is_active=True).first()
    if user is None:
        return  # Avoid wrongly assigning ownership.
    if PurchaseOrder.objects.filter(user_id=user.pk, request_key=REQUEST_KEY).exists():
        return
    items = json.loads(zlib.decompress(base64.b64decode(DATA)).decode("utf-8"))
    if len(items) != 67 or sum(x["quantity"] for x in items) != 10300:
        raise ValueError("China purchase source totals do not match")
    amount = sum((Decimal(x["cost"]) * x["quantity"] for x in items), Decimal(0))
    if amount.quantize(Decimal("0.01")) != Decimal("7806887.06"):
        raise ValueError("China purchase source amount does not match")
    supplier, _ = Supplier.objects.get_or_create(name="Китай — поставщик уточняется")
    PurchaseOrder.objects.create(
        user_id=user.pk, supplier_id=supplier.pk,
        request_key=REQUEST_KEY, title="Китай · первый закуп",
        items=items, original_items=items, estimated_total=amount,
        destination="PP2", status="draft", delivery_stage="draft",
        comment=("Исторический закупочный список. Дата закупки, юридическое название "
                 "поставщика, оплата, склад получения и факт поставки в файле не указаны. "
                 "PP2 — только техническое значение поля, не подтверждённый склад. "
                 "Не проводить приход по этому файлу. "
                 "Источник: Первый закуп с Китая(3).xlsx; SHA256: " + SOURCE_SHA256),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0116_profitable_offer_record"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.RunPython(import_first_china_purchase, migrations.RunPython.noop)]
