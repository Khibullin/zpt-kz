import uuid

from django.db import migrations, models


def populate_order_access_tokens(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    for order in Order.objects.all():
        order.access_token = uuid.uuid4()
        order.save(update_fields=['access_token'])


def migrate_order_statuses(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(status='pending_payment').update(status='awaiting_payment')
    Order.objects.filter(status='canceled').update(status='cancelled')


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_cartitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='seller_name',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Продавец'),
        ),
        migrations.AddField(
            model_name='order',
            name='seller_whatsapp',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='WhatsApp продавца'),
        ),
        migrations.AddField(
            model_name='order',
            name='access_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True, verbose_name='Токен доступа'),
        ),
        migrations.RunPython(populate_order_access_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='access_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен доступа'),
        ),
        migrations.RunPython(migrate_order_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('new', 'Новый'),
                    ('confirmed', 'Подтверждён продавцом'),
                    ('awaiting_payment', 'Ожидает оплаты'),
                    ('paid', 'Оплачен'),
                    ('cancelled', 'Отменён'),
                ],
                default='new',
                max_length=20,
                verbose_name='Статус',
            ),
        ),
    ]
