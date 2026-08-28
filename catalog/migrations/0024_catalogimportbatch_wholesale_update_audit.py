import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0023_configure_ag_parts_wholesale_terms'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogimportbatch',
            name='uploaded_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='catalog_import_batches_uploaded',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Загрузил',
            ),
        ),
        migrations.AddField(
            model_name='catalogimportbatch',
            name='applied_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='catalog_import_batches_applied',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Применил',
            ),
        ),
    ]
