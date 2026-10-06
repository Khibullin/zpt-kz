import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def populate_service_request_access_tokens(apps, schema_editor):
    """Give every existing request its own uuid4.

    A column DEFAULT would be evaluated once for the whole table and would
    collide on unique=True. uuid.uuid4() is called per row instead.
    """
    ServiceRequest = apps.get_model('service_requests', 'ServiceRequest')
    for req in ServiceRequest.objects.filter(access_token__isnull=True).iterator():
        req.access_token = uuid.uuid4()
        req.save(update_fields=['access_token'])


def clear_service_request_access_tokens(apps, schema_editor):
    ServiceRequest = apps.get_model('service_requests', 'ServiceRequest')
    ServiceRequest.objects.update(access_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ('service_requests', '0007_alter_service_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='serviceseller',
            name='user',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='service_seller_profile',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Учётная запись',
            ),
        ),
        migrations.AddField(
            model_name='servicerequest',
            name='access_token',
            field=models.UUIDField(
                db_index=True,
                editable=False,
                null=True,
                verbose_name='Токен доступа',
            ),
        ),
        migrations.RunPython(
            populate_service_request_access_tokens,
            clear_service_request_access_tokens,
        ),
        migrations.AlterField(
            model_name='servicerequest',
            name='access_token',
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name='Токен доступа',
            ),
        ),
    ]
