from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0040_seller_request_access'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contactconsent',
            name='source',
            field=models.CharField(
                blank=True,
                choices=[
                    ('request_form', 'Форма заявки'),
                    ('registration', 'Регистрация'),
                    ('buyer_portal', 'Buyer portal'),
                    ('whatsapp', 'WhatsApp'),
                    ('admin', 'Админ'),
                    ('import', 'Импорт'),
                    ('seller_portal', 'Кабинет продавца'),
                    ('seller_req_page', 'Страница заявки продавца'),
                ],
                default='',
                max_length=20,
                verbose_name='Источник',
            ),
        ),
        migrations.AlterField(
            model_name='sellercontactconsent',
            name='source',
            field=models.CharField(
                blank=True,
                choices=[
                    ('request_form', 'Форма заявки'),
                    ('registration', 'Регистрация'),
                    ('buyer_portal', 'Buyer portal'),
                    ('whatsapp', 'WhatsApp'),
                    ('admin', 'Админ'),
                    ('import', 'Импорт'),
                    ('seller_portal', 'Кабинет продавца'),
                    ('seller_req_page', 'Страница заявки продавца'),
                ],
                default='',
                max_length=20,
                verbose_name='Источник',
            ),
        ),
    ]
