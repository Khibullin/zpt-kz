from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0029_ag_parts_stage3_air_filter"),
    ]

    operations = [
        migrations.CreateModel(
            name="KaspiRepricerRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("OBSERVE", "Наблюдение"), ("MANUAL", "С подтверждением"), ("AUTO", "Автомат")], default="OBSERVE", max_length=16, verbose_name="Режим")),
                ("min_price", models.DecimalField(decimal_places=2, help_text="Ни одна рекомендация не может быть ниже этой цены.", max_digits=14, validators=[MinValueValidator(0)], verbose_name="MIN цена")),
                ("min_margin_percent", models.DecimalField(blank=True, decimal_places=2, help_text="Зарезервировано для финансового слоя; MIN цена остаётся жёстким ограничением.", max_digits=6, null=True, validators=[MinValueValidator(0)], verbose_name="Минимальная маржа, %")),
                ("price_step", models.DecimalField(decimal_places=2, default=1, max_digits=10, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Шаг цены")),
                ("max_change_percent", models.DecimalField(decimal_places=2, default=10, max_digits=6, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Макс. изменение за расчёт, %")),
                ("allow_raise", models.BooleanField(default=True, verbose_name="Разрешать повышение")),
                ("is_enabled", models.BooleanField(db_index=True, default=True, verbose_name="Включён")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("listing", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="repricer_rule", to="catalog.productkaspilisting", verbose_name="Kaspi-привязка")),
            ],
            options={
                "verbose_name": "Правило репрайсера Kaspi",
                "verbose_name_plural": "Правила репрайсера Kaspi",
                "ordering": ("listing__product__sku",),
            },
        ),
        migrations.CreateModel(
            name="KaspiCompetitorOfferSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("seller_name", models.CharField(max_length=255, verbose_name="Продавец")),
                ("seller_code", models.CharField(blank=True, max_length=128, verbose_name="Код продавца")),
                ("price", models.DecimalField(decimal_places=2, max_digits=14, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Цена")),
                ("position", models.PositiveIntegerField(blank=True, null=True, verbose_name="Позиция")),
                ("is_available", models.BooleanField(default=True, verbose_name="В наличии")),
                ("source", models.CharField(default="manual", max_length=64, verbose_name="Источник")),
                ("captured_at", models.DateTimeField(db_index=True, verbose_name="Получено")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="competitor_offer_snapshots", to="catalog.productkaspilisting", verbose_name="Kaspi-привязка")),
            ],
            options={
                "verbose_name": "Цена конкурента Kaspi",
                "verbose_name_plural": "Цены конкурентов Kaspi",
                "ordering": ("-captured_at", "price"),
            },
        ),
        migrations.CreateModel(
            name="KaspiOwnPriceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price", models.DecimalField(decimal_places=2, max_digits=14, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Наша цена")),
                ("source", models.CharField(default="sync", max_length=64, verbose_name="Источник")),
                ("captured_at", models.DateTimeField(db_index=True, verbose_name="Получено")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="own_price_snapshots", to="catalog.productkaspilisting", verbose_name="Kaspi-привязка")),
            ],
            options={
                "verbose_name": "История нашей цены Kaspi",
                "verbose_name_plural": "История наших цен Kaspi",
                "ordering": ("-captured_at",),
            },
        ),
        migrations.CreateModel(
            name="KaspiRepricerRecommendation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("current_price", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Текущая цена")),
                ("best_competitor_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="Лучшая цена конкурента")),
                ("recommended_price", models.DecimalField(decimal_places=2, max_digits=14, verbose_name="Рекомендованная цена")),
                ("market_position", models.PositiveIntegerField(blank=True, null=True, verbose_name="Наша позиция")),
                ("action", models.CharField(choices=[("LOWER", "Снизить"), ("RAISE", "Повысить"), ("HOLD", "Оставить")], max_length=16, verbose_name="Действие")),
                ("reason_code", models.CharField(max_length=64, verbose_name="Код причины")),
                ("reason", models.CharField(max_length=500, verbose_name="Причина")),
                ("status", models.CharField(choices=[("PENDING", "Ожидает"), ("APPLIED", "Применено"), ("DISMISSED", "Отклонено")], db_index=True, default="PENDING", max_length=16, verbose_name="Статус")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создано")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="Закрыто")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="repricer_recommendations", to="catalog.productkaspilisting", verbose_name="Kaspi-привязка")),
                ("rule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recommendations", to="repricer.kaspirepricerrule", verbose_name="Правило")),
            ],
            options={
                "verbose_name": "Рекомендация репрайсера Kaspi",
                "verbose_name_plural": "Рекомендации репрайсера Kaspi",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="kaspicompetitoroffersnapshot",
            index=models.Index(fields=["listing", "captured_at"], name="repr_offer_time_idx"),
        ),
        migrations.AddIndex(
            model_name="kaspicompetitoroffersnapshot",
            index=models.Index(fields=["listing", "price"], name="repr_offer_price_idx"),
        ),
        migrations.AddIndex(
            model_name="kaspiownpricesnapshot",
            index=models.Index(fields=["listing", "captured_at"], name="repr_own_time_idx"),
        ),
        migrations.AddIndex(
            model_name="kaspirepricerrecommendation",
            index=models.Index(fields=["listing", "created_at"], name="repr_rec_time_idx"),
        ),
    ]
