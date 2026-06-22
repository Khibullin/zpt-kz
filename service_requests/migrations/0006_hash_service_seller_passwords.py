from django.contrib.auth.hashers import make_password
from django.db import migrations


def _looks_hashed(value):
    if not value:
        return False

    return value.startswith(
        (
            'pbkdf2_',
            'argon2',
            'bcrypt',
            'scrypt',
        )
    )


def hash_plain_passwords(apps, schema_editor):
    ServiceSeller = apps.get_model('service_requests', 'ServiceSeller')

    for seller in ServiceSeller.objects.all().iterator():
        password = seller.password or ''

        if not password or _looks_hashed(password):
            continue

        seller.password = make_password(password)
        seller.save(update_fields=['password'])


class Migration(migrations.Migration):

    dependencies = [
        ('service_requests', '0005_serviceseller_description_serviceseller_instagram_and_more'),
    ]

    operations = [
        migrations.RunPython(
            hash_plain_passwords,
            migrations.RunPython.noop,
        ),
    ]
