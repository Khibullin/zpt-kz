# Generated manually for stage 2.5 security audit

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0005_marketing_campaign_send'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='marketingcampaignsendrun',
            constraint=models.UniqueConstraint(
                condition=models.Q(('mode', 'TEST'), ('status', 'running')),
                fields=('campaign',),
                name='marketing_campaign_one_running_test_send',
            ),
        ),
    ]
