# Generated manually for stage 2.9B: simple mailing LIVE waves

from django.db import migrations, models


ACTIVE_SIMPLE_MAILING_LOCK_VALUE = 1


def backfill_message_wave_fields(apps, schema_editor):
    Message = apps.get_model('marketing', 'MarketingCampaignMessage')
    send_run_positions: dict[int, int] = {}
    for message in Message.objects.order_by('send_run_id', 'id').iterator():
        send_run_id = message.send_run_id
        position = send_run_positions.get(send_run_id, 0) + 1
        send_run_positions[send_run_id] = position
        Message.objects.filter(pk=message.pk).update(
            wave_number=1,
            position_number=position,
            scheduled_at=message.created_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0008_marketing_live_send'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketingcampaignsendrun',
            name='workflow_type',
            field=models.CharField(
                choices=[
                    ('legacy', 'Legacy campaign'),
                    ('simple_mailing', 'Simple mailing'),
                ],
                default='legacy',
                max_length=32,
                verbose_name='Тип workflow',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignsendrun',
            name='simple_mailing_key',
            field=models.UUIDField(
                blank=True,
                null=True,
                unique=True,
                verbose_name='Ключ idempotency simple mailing',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignsendrun',
            name='active_simple_mailing_lock',
            field=models.PositiveSmallIntegerField(
                blank=True,
                default=None,
                null=True,
                verbose_name='Блокировка active simple mailing',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignmessage',
            name='wave_number',
            field=models.PositiveIntegerField(
                default=1,
                null=True,
                verbose_name='Номер волны',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignmessage',
            name='position_number',
            field=models.PositiveIntegerField(
                default=1,
                null=True,
                verbose_name='Позиция в snapshot',
            ),
        ),
        migrations.AddField(
            model_name='marketingcampaignmessage',
            name='scheduled_at',
            field=models.DateTimeField(
                null=True,
                verbose_name='Не раньше',
            ),
        ),
        migrations.RunPython(
            backfill_message_wave_fields,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='marketingcampaignmessage',
            name='wave_number',
            field=models.PositiveIntegerField(default=1, verbose_name='Номер волны'),
        ),
        migrations.AlterField(
            model_name='marketingcampaignmessage',
            name='position_number',
            field=models.PositiveIntegerField(default=1, verbose_name='Позиция в snapshot'),
        ),
        migrations.AlterField(
            model_name='marketingcampaignmessage',
            name='scheduled_at',
            field=models.DateTimeField(verbose_name='Не раньше'),
        ),
        migrations.AddIndex(
            model_name='marketingcampaignmessage',
            index=models.Index(
                fields=['status', 'scheduled_at'],
                name='marketing_msg_due_queue',
            ),
        ),
        migrations.AddIndex(
            model_name='marketingcampaignmessage',
            index=models.Index(
                fields=['send_run', 'wave_number'],
                name='marketing_msg_run_wave',
            ),
        ),
        migrations.AddConstraint(
            model_name='marketingcampaignmessage',
            constraint=models.UniqueConstraint(
                fields=('send_run', 'position_number'),
                name='marketing_campaign_message_unique_position_run',
            ),
        ),
        migrations.AddConstraint(
            model_name='marketingcampaignsendrun',
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ('active_simple_mailing_lock', ACTIVE_SIMPLE_MAILING_LOCK_VALUE),
                ),
                fields=('active_simple_mailing_lock',),
                name='uniq_active_simple_mailing_lock',
            ),
        ),
    ]
