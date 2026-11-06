from django.conf import settings
from django.db import migrations


def grant_manager(apps, schema_editor):
    db = schema_editor.connection.alias
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    # Address only the existing, activated procurement manager identified by owner.
    user = User.objects.using(db).filter(
        username='rasidhajbullin42@gmail.com', is_active=True,
        membership__active=True, membership__role='sales',
    ).first()
    content_type, _ = ContentType.objects.using(db).get_or_create(
        app_label='inventory', model='stockdocument',
    )
    permission, _ = Permission.objects.using(db).get_or_create(
        content_type=content_type, codename='manage_accounting',
        defaults={'name': 'Работа в учёте товаров и денег в качестве менеджера'},
    )
    if user:
        user.user_permissions.add(permission)
        apps.get_model('procurement', 'Activity').objects.using(db).create(
            user_id=user.pk, action='accounting_manager_access_granted',
            details={'source': 'owner_request_2026_11_06', 'role': 'sales'},
        )


def revoke_manager(apps, schema_editor):
    db = schema_editor.connection.alias
    User = apps.get_model(settings.AUTH_USER_MODEL)
    for user in User.objects.using(db).filter(username='rasidhajbullin42@gmail.com'):
        user.user_permissions.remove(*user.user_permissions.filter(
            content_type__app_label='inventory', codename='manage_accounting',
        ))


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0009_cashentry_purchase_order_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterModelOptions(
            name='stockdocument',
            options={'permissions': [('manage_accounting', 'Работа в учёте товаров и денег в качестве менеджера')]},
        ),
        migrations.RunPython(grant_manager, revoke_manager),
    ]
