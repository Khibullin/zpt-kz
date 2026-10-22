from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0123_import_parts_12_cities_20261019'),
    ]

    operations = [
        migrations.AddField(
            model_name='requestdispatch',
            name='delivery_channel',
            field=models.CharField(
                choices=[('meta', 'Meta API'), ('manual_whatsapp', 'Ручной WhatsApp')],
                db_index=True, default='meta', max_length=20,
                verbose_name='Канал отправки',
            ),
        ),
        migrations.AddField(
            model_name='requestdispatch',
            name='permission_evidence',
            field=models.CharField(
                blank=True, default='', max_length=300,
                verbose_name='Разрешение на разовую отправку',
            ),
        ),
        migrations.AlterField(
            model_name='requestdispatch',
            name='status',
            field=models.CharField(
                choices=[
                    ('prepared', 'Подготовлено вручную'),
                    ('queued', 'В очереди'),
                    ('sent', 'Отправлено'),
                    ('paused', 'Остановлено'),
                    ('failed', 'Ошибка'),
                ],
                default='queued', max_length=20, verbose_name='Статус',
            ),
        ),
        migrations.AlterField(
            model_name='contactconsent',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('service', 'Сервисные'),
                    ('information', 'Информационные'),
                    ('marketing', 'Рекламные'),
                    ('buyer_requests', 'Заявки покупателей'),
                ],
                max_length=16, verbose_name='Цель',
            ),
        ),
        migrations.AlterField(
            model_name='sellercontactconsent',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('service', 'Сервисные'),
                    ('information', 'Информационные'),
                    ('marketing', 'Рекламные'),
                    ('buyer_requests', 'Заявки покупателей'),
                ],
                max_length=16, verbose_name='Цель',
            ),
        ),
        migrations.AlterField(
            model_name='sellerrequestpageevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('page_open', 'Открытие страницы'),
                    ('whatsapp_click', 'Клик WhatsApp'),
                    ('call_click', 'Клик звонка'),
                    ('marketing_consent_yes', 'Согласие на предложения'),
                    ('marketing_consent_no', 'Отказ от предложений'),
                    ('requests_yes', 'Подписка на заявки покупателей'),
                    ('requests_no', 'Отказ от заявок покупателей'),
                    ('out_of_stock', 'Нет в наличии'),
                    ('cannot_fulfill', 'Не могу выполнить заявку'),
                ],
                max_length=32, verbose_name='Тип события',
            ),
        ),
    ]
