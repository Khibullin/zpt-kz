from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0034_kaspi_listing_public_url"),
        ("repricer", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="KaspiCompetitorIngestBatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("external_batch_id", models.UUIDField(unique=True, verbose_name="Внешний batch id")),
                (
                    "source",
                    models.CharField(
                        default="office_collector",
                        max_length=64,
                        verbose_name="Источник",
                    ),
                ),
                ("captured_at", models.DateTimeField(verbose_name="Получено")),
                ("offer_count", models.PositiveIntegerField(verbose_name="Число офферов")),
                (
                    "received_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Принято"),
                ),
                (
                    "listing",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="competitor_ingest_batches",
                        to="catalog.productkaspilisting",
                        verbose_name="Kaspi-привязка",
                    ),
                ),
            ],
            options={
                "verbose_name": "Пакет цен конкурентов Kaspi",
                "verbose_name_plural": "Пакеты цен конкурентов Kaspi",
                "ordering": ("-received_at",),
            },
        ),
        migrations.AddIndex(
            model_name="kaspicompetitoringestbatch",
            index=models.Index(
                fields=("listing", "received_at"),
                name="repr_ingest_listing_idx",
            ),
        ),
    ]
