from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ReviewedPeugeotMigrationTests(TransactionTestCase):
    migrate_from = ('core', '0077_editorialdailyexecution')
    migrate_to = ('core', '0078_publish_reviewed_peugeot_article')

    def test_migration_does_not_create_any_new_articles(self):
        from core.models import EditorialPage
        # On an empty test DB there is no matching sourced draft.
        self.assertFalse(EditorialPage.objects.filter(slug='part-guide-2').exists())
