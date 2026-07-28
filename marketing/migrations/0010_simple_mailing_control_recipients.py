from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0009_simple_mailing_live_waves'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketingcampaignrecipient',
            name='is_control_recipient',
            field=models.BooleanField(
                default=False,
                verbose_name='Контрольный получатель',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignsendrun',
            name='recipient_scope',
            field=models.CharField(
                blank=True,
                choices=[
                    ('control_only', 'Контрольная рассылка'),
                    ('audience_plus_controls', 'Рабочая рассылка'),
                ],
                default='',
                max_length=32,
                verbose_name='Область получателей simple mailing',
            ),
        ),
    ]
