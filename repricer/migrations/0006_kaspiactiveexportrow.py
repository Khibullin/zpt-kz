from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("repricer", "0005_kaspimanualpricedraft"),
    ]
    operations = [
        migrations.CreateModel(
            name="KaspiActiveExportRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_number", models.PositiveIntegerField()),
                ("master_sku", models.CharField(blank=True, db_index=True, max_length=128)),
                ("merchant_sku", models.CharField(blank=True, max_length=128)),
                ("product_name", models.CharField(blank=True, max_length=500)),
                ("brand", models.CharField(blank=True, max_length=150)),
                ("price", models.PositiveIntegerField(blank=True, null=True)),
                ("quantity", models.PositiveIntegerField(blank=True, null=True)),
                ("status", models.CharField(db_index=True, max_length=40)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("batch", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="repricer_active_rows", to="catalog.catalogimportbatch",
                )),
                ("listing", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="active_export_rows", to="catalog.productkaspilisting",
                )),
            ],
            options={
                "verbose_name": "Строка выгрузки ACTIVE Kaspi",
                "verbose_name_plural": "Строки выгрузки ACTIVE Kaspi",
                "ordering": ("row_number", "pk"),
            },
        ),
        migrations.AddConstraint(
            model_name="kaspiactiveexportrow",
            constraint=models.UniqueConstraint(
                fields=("batch", "row_number"), name="repr_active_batch_row_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="kaspiactiveexportrow",
            index=models.Index(
                fields=["batch", "status"], name="repr_active_batch_status_idx",
            ),
        ),
    ]
