from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('service_requests', '0003_servicebroadcastsettings_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceWhatsAppMessageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),

                ('phone', models.CharField(max_length=30)),

                ('message_type', models.CharField(
                    choices=[
                        ('seller_request', 'Заявка исполнителю'),
                        ('buyer_notice', 'Уведомление клиенту'),
                        ('manual', 'Ручная отправка'),
                    ],
                    default='seller_request',
                    max_length=30
                )),

                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('sent', 'Sent'),
                        ('failed', 'Failed'),
                    ],
                    default='pending',
                    max_length=20
                )),

                ('meta_message_id', models.CharField(blank=True, max_length=255)),
                ('error_text', models.TextField(blank=True)),
                ('response_json', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),

                ('request', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='wa_logs',
                    to='service_requests.servicerequest'
                )),

                ('seller', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='wa_logs',
                    to='service_requests.serviceseller'
                )),
            ],
            options={
                'verbose_name': 'WhatsApp лог исполнителя',
                'verbose_name_plural': 'WhatsApp логи исполнителей',
                'ordering': ['-created_at'],
            },
        ),
    ]