"""Regression checks for durable manual Apify intake (no external Apify calls)."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .apify_batch_intake import stage_apify_file, process_next_apify_batch
from .models import Activity


class ApifyBatchIntakeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='apify_operator', password='test-only')

    def test_exact_source_rows_and_500_checkpoints(self):
        rows = [{'id': str(i), 'extra': {'original': [i, None, False]}}
                for i in range(501)]
        file_bytes = json.dumps(rows).encode('utf-8')
        source = SimpleUploadedFile('export.json', file_bytes)
        session = stage_apify_file(source, self.user)
        self.assertEqual(session.details['total'], 501)
        self.assertEqual(session.details['total_batches'], 2)
        batches = list(Activity.objects.filter(
            action='apify_upload_source_batch', details__session_id=session.pk)
            .order_by('id'))
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0].details['rows']), 500)
        self.assertEqual(len(batches[1].details['rows']), 1)
        self.assertEqual(batches[1].details['rows'][0], rows[-1])

        with patch('procurement.apify_batch_intake.import_apify_results',
                   return_value={'products': 1, 'linked': 1, 'verified': 0,
                                 'unmatched': 0, 'invalid': 0} ) as importer:
            p1 = process_next_apify_batch(session.pk, self.user)
            self.assertFalse(p1['done'])
            self.assertEqual(p1['next_batch'], 1)
            p2 = process_next_apify_batch(session.pk, self.user)
            self.assertTrue(p2['done'])
            self.assertEqual(p2['next_batch'], 2)
            process_next_apify_batch(session.pk, self.user)
            self.assertEqual(importer.call_count, 2)

    def test_reupload_uses_existing_session(self):
        data = b'[{"id":"123456"}]'
        first = stage_apify_file(SimpleUploadedFile('a.json', data), self.user)
        second = stage_apify_file(SimpleUploadedFile('a.json', data), self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Activity.objects.filter(action='apify_upload_session').count(), 1)
