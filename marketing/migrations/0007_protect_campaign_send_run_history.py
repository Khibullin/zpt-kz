# Generated for stage 2.5: preserve send history on campaign delete

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0006_marketing_campaign_send_constraints'),
    ]

    operations = [
        migrations.AlterField(
            model_name='marketingcampaignsendrun',
            name='campaign',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='send_runs',
                to='marketing.marketingcampaign',
                verbose_name='Кампания',
            ),
        ),
    ]
