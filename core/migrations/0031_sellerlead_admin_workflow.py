# Generated manually for SellerLead admin workflow

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_buyer_broadcast_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='sellerlead',
            name='marketplace_invitation_planned_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Дата планирования приглашения в маркетплейс',
            ),
        ),
        migrations.AddField(
            model_name='sellerlead',
            name='marketplace_invitation_status',
            field=models.CharField(
                blank=True,
                choices=[('', 'Не запланировано'), ('planned', 'Приглашение запланировано')],
                default='',
                max_length=16,
                verbose_name='Приглашение в маркетплейс',
            ),
        ),
        migrations.AddField(
            model_name='sellerlead',
            name='rejected_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Дата отклонения',
            ),
        ),
        migrations.AddField(
            model_name='sellerlead',
            name='request_seller',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='seller_leads',
                to='core.seller',
                verbose_name='Продавец заявок',
            ),
        ),
        migrations.AddField(
            model_name='sellerlead',
            name='review_status',
            field=models.CharField(
                choices=[
                    ('needs_review', 'Требует проверки'),
                    ('converted_requests', 'Добавлен в продавцы заявок'),
                    ('marketplace_planned', 'Отмечен для приглашения в маркетплейс'),
                    ('converted_and_marketplace_planned', 'Заявки + маркетплейс'),
                    ('rejected', 'Отклонён'),
                ],
                default='needs_review',
                max_length=40,
                verbose_name='Статус обработки',
            ),
        ),
        migrations.AddField(
            model_name='sellerlead',
            name='reviewed_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Дата обработки администратором',
            ),
        ),
    ]
