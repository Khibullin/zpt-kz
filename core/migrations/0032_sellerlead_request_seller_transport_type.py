# Generated manually for SellerLead request seller transport type

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_sellerlead_admin_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='sellerlead',
            name='request_seller_transport_type',
            field=models.CharField(
                blank=True,
                choices=[('car', 'Легковые'), ('truck', 'Грузовые')],
                default='',
                help_text='Обязателен перед созданием нового продавца заявок из SellerLead.',
                max_length=10,
                verbose_name='Тип транспорта для продавца заявок',
            ),
        ),
    ]
