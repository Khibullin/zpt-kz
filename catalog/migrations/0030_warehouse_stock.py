from django.db import migrations, models
import django.db.models.deletion


def seed_default_warehouses(apps, schema_editor):
    Warehouse = apps.get_model('catalog', 'Warehouse')
    for code, name in (
        ('PP1', 'Основной склад'),
        ('PP2', 'Fulfillment'),
    ):
        Warehouse.objects.get_or_create(
            code=code,
            defaults={'name': name, 'is_active': True},
        )


def unseed_default_warehouses(apps, schema_editor):
    Warehouse = apps.get_model('catalog', 'Warehouse')
    Warehouse.objects.filter(code__in=('PP1', 'PP2')).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0029_ag_parts_stage3_air_filter'),
    ]

    operations = [
        migrations.CreateModel(
            name='Warehouse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=32, unique=True, verbose_name='Код склада')),
                ('name', models.CharField(max_length=128, verbose_name='Название')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Склад',
                'verbose_name_plural': 'Склады',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='ProductWarehouseStock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(default=0, verbose_name='Остаток')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='warehouse_stocks',
                    to='catalog.product',
                    verbose_name='Товар',
                )),
                ('warehouse', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='stocks',
                    to='catalog.warehouse',
                    verbose_name='Склад',
                )),
            ],
            options={
                'verbose_name': 'Остаток на складе',
                'verbose_name_plural': 'Остатки на складах',
                'ordering': ['product_id', 'warehouse_id'],
            },
        ),
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('movement_type', models.CharField(
                    choices=[
                        ('OPENING', 'Открытие'),
                        ('RECEIPT', 'Приход'),
                        ('SALE', 'Продажа'),
                        ('TRANSFER_IN', 'Перемещение (вход)'),
                        ('TRANSFER_OUT', 'Перемещение (выход)'),
                        ('ADJUSTMENT', 'Корректировка'),
                        ('RETURN', 'Возврат'),
                    ],
                    db_index=True,
                    max_length=16,
                    verbose_name='Тип движения',
                )),
                ('quantity_delta', models.IntegerField(verbose_name='Изменение')),
                ('quantity_before', models.PositiveIntegerField(verbose_name='Было')),
                ('quantity_after', models.PositiveIntegerField(verbose_name='Стало')),
                ('source', models.CharField(default='', max_length=64, verbose_name='Источник')),
                ('reference', models.CharField(
                    blank=True,
                    db_index=True,
                    default='',
                    max_length=128,
                    verbose_name='Ссылка / документ',
                )),
                ('note', models.TextField(blank=True, default='', verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Создано')),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stock_movements',
                    to='catalog.product',
                    verbose_name='Товар',
                )),
                ('warehouse', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='stock_movements',
                    to='catalog.warehouse',
                    verbose_name='Склад',
                )),
            ],
            options={
                'verbose_name': 'Движение склада',
                'verbose_name_plural': 'Движения склада',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='productwarehousestock',
            constraint=models.UniqueConstraint(
                fields=('product', 'warehouse'),
                name='uniq_product_warehouse_stock',
            ),
        ),
        migrations.AddConstraint(
            model_name='productwarehousestock',
            constraint=models.CheckConstraint(
                condition=models.Q(('quantity__gte', 0)),
                name='catalog_warehouse_stock_qty_gte_0',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(
                fields=['warehouse', 'movement_type'],
                name='cat_stock_move_wh_type_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(
                fields=['product', 'created_at'],
                name='cat_stock_move_prod_dt_idx',
            ),
        ),
        migrations.RunPython(seed_default_warehouses, unseed_default_warehouses),
    ]
