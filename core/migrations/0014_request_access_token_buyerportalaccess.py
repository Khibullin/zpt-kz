import uuid

from django.db import migrations, models


def populate_request_tokens(apps, schema_editor):
    Request = apps.get_model('core', 'Request')
    for req in Request.objects.all():
        req.access_token = uuid.uuid4()
        req.save(update_fields=['access_token'])


def populate_buyer_portal_access(apps, schema_editor):
    Request = apps.get_model('core', 'Request')
    BuyerPortalAccess = apps.get_model('core', 'BuyerPortalAccess')

    seen_phones = set()
    for req in Request.objects.order_by('created_at'):
        phone = ''.join(ch for ch in str(req.phone or '') if ch.isdigit())
        if not phone or phone in seen_phones:
            continue
        seen_phones.add(phone)
        BuyerPortalAccess.objects.get_or_create(
            phone_normalized=phone,
            defaults={'access_token': uuid.uuid4()},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_requestphoto'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuyerPortalAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_normalized', models.CharField(db_index=True, max_length=20, unique=True, verbose_name='Нормализованный телефон')),
                ('access_token', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен истории заявок')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Доступ покупателя к истории',
                'verbose_name_plural': 'Доступы покупателей к истории',
            },
        ),
        migrations.AddField(
            model_name='request',
            name='access_token',
            field=models.UUIDField(db_index=True, editable=False, null=True, verbose_name='Токен доступа покупателя'),
        ),
        migrations.RunPython(populate_request_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='request',
            name='access_token',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True, verbose_name='Токен доступа покупателя'),
        ),
        migrations.RunPython(populate_buyer_portal_access, migrations.RunPython.noop),
    ]
