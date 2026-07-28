from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0010_simple_mailing_control_recipients'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='marketingcampaignmessage',
            options={
                'ordering': ('id',),
                'verbose_name': 'WhatsApp-лог маркетинговой рассылки',
                'verbose_name_plural': 'WhatsApp-логи маркетинговых рассылок',
            },
        ),
        migrations.AlterModelOptions(
            name='marketingcampaignsendrun',
            options={
                'ordering': ('-created_at', '-id'),
                'verbose_name': 'Запуск маркетинговой рассылки',
                'verbose_name_plural': 'Запуски маркетинговых рассылок',
            },
        ),
        migrations.AlterModelOptions(
            name='marketingwhatsapptemplate',
            options={
                'ordering': ('-updated_at', '-id'),
                'verbose_name': 'Маркетинговый WhatsApp-шаблон',
                'verbose_name_plural': 'WhatsApp-шаблоны маркетинга',
            },
        ),
    ]
