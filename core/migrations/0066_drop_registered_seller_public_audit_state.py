from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_seller_directory_consistency_cleanup'),
    ]

    operations = [
        migrations.RunSQL(
            'DROP TABLE IF EXISTS core_registered_seller_public_audit;',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
