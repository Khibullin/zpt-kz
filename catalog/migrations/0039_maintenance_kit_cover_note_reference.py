from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0038_maintenance_kit_car_request_phone_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='maintenancekit',
            name='cover_note',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    'Показывается под общим фото, если оно не совпадает с составом '
                    'заказа. Исходный файл фото не менять.'
                ),
                max_length=255,
                verbose_name='Подпись к общему фото',
            ),
        ),
        migrations.AddField(
            model_name='maintenancekit',
            name='reference_lines',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Не входят в корзину и итоговую сумму. Список объектов: '
                    'type_label, article (пусто = OEM неизвестен), note.'
                ),
                verbose_name='Справочные позиции',
            ),
        ),
    ]
