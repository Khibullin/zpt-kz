from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("catalog", "0091_stage_twenty_nine_reviewed_aywiparts_sources")]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "catalog_photo_status_id_idx "
                "ON catalog_supplierphototask (status, id)"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "catalog_photo_status_id_idx"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "catalog_photo_status_batch_id_idx "
                "ON catalog_supplierphototask "
                "(status, (evidence -> 'reviewed_batch'), id)"
            ),
            reverse_sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "catalog_photo_status_batch_id_idx"
            ),
        ),
    ]
