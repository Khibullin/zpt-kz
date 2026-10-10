from django.db import migrations, models


def mark_legacy(apps, schema_editor):
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    Listing.objects.using(schema_editor.connection.alias).filter(
        public_url_source="", public_url__startswith="https://"
    ).update(public_url_source="legacy")


class Migration(migrations.Migration):
    dependencies = [("catalog", "0059_sync_kaspi_prices_20261010")]
    operations = [
        migrations.AddField(
            model_name="productkaspilisting",
            name="public_url_source",
            field=models.CharField(max_length=32, blank=True, default="", verbose_name="Источник ссылки Kaspi",
                                   help_text="kaspi_pay_copy — ссылка из кабинета продавца; legacy — историческая ссылка."),
        ),
        migrations.AddField(
            model_name="productkaspilisting",
            name="public_url_verified_at",
            field=models.DateTimeField(null=True, blank=True, verbose_name="Дата подтверждения ссылки Kaspi"),
        ),
        migrations.RunPython(mark_legacy, migrations.RunPython.noop),
    ]
