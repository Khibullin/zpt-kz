from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("repricer", "0007_kaspirepricersearchsettings")]

    operations = [
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_min_price",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_seller",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_seller_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_city",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="competitor_scan_status",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name="KaspiActiveCompetitorOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seller_id", models.CharField(max_length=128)),
                ("seller_name", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(max_length=80)),
                ("price", models.PositiveIntegerField()),
                ("product_url", models.URLField(blank=True, max_length=500)),
                ("checked_at", models.DateTimeField()),
                ("active_row", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="verified_competitor_offers", to="repricer.kaspiactiveexportrow",
                )),
            ],
            options={"ordering": ("price", "pk")},
        ),
        migrations.AddIndex(
            model_name="kaspiactivecompetitoroffer",
            index=models.Index(fields=["active_row", "price"], name="repr_active_offer_price_idx"),
        ),
    ]
