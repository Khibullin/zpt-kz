from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("repricer", "0004_kaspimanualcollectionrun"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="KaspiManualPriceDraft",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("price", models.PositiveIntegerField(verbose_name="Подготовленная цена")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("listing", models.OneToOneField(to="catalog.productkaspilisting", on_delete=django.db.models.deletion.CASCADE, related_name="manual_price_draft")),
                ("updated_by", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
            ],
            options={"verbose_name": "Черновик цены Kaspi", "verbose_name_plural": "Черновики цен Kaspi"},
        ),
    ]
