from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0150_keep_latest_omega_price_update_only"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierComparisonPriceList",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("supplier_code", models.CharField(max_length=24, unique=True)),
                ("filename", models.CharField(max_length=255)),
                ("checked_on", models.DateField()),
                ("uploaded_at", models.DateTimeField(auto_now=True)),
                ("row_count", models.PositiveIntegerField(default=0)),
                ("available_count", models.PositiveIntegerField(default=0)),
                ("skipped_count", models.PositiveIntegerField(default=0)),
                ("rows", models.JSONField(default=list)),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["supplier_code"]},
        ),
    ]
