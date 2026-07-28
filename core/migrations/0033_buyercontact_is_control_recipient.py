from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_sellerlead_request_seller_transport_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='buyercontact',
            name='is_control_recipient',
            field=models.BooleanField(
                default=False,
                help_text='Внутренний контрольный номер для проверки маркетинговых WhatsApp-рассылок.',
                verbose_name='Контрольный получатель',
            ),
        ),
    ]
