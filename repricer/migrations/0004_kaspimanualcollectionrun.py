from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("repricer", "0003_city_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KaspiManualCollectionRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("city_ids", models.JSONField(default=list, verbose_name="Города")),
                ("listing_ids", models.JSONField(default=list, verbose_name="Kaspi-привязки")),
                ("status", models.CharField(choices=[("running", "В процессе"), ("done", "Завершено"), ("stopped", "Остановлено")], default="running", max_length=12, verbose_name="Состояние")),
                ("next_index", models.PositiveIntegerField(default=0, verbose_name="Следующее задание")),
                ("saved_offers", models.PositiveIntegerField(default=0, verbose_name="Предложений сохранено")),
                ("empty_count", models.PositiveIntegerField(default=0, verbose_name="Без предложений")),
                ("skipped_count", models.PositiveIntegerField(default=0, verbose_name="Без привязки")),
                ("error_count", models.PositiveIntegerField(default=0, verbose_name="Ошибок")),
                ("last_error", models.CharField(blank=True, max_length=500, verbose_name="Последняя ошибка")),
                ("started_at", models.DateTimeField(auto_now_add=True, verbose_name="Начато")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="Завершено")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="kaspi_manual_collections", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Ручной сбор предложений Kaspi",
                "verbose_name_plural": "Ручные сборы предложений Kaspi",
                "ordering": ("-started_at",),
            },
        ),
    ]
