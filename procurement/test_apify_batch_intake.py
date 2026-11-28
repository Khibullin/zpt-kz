"""Regression checks for durable manual Apify intake (no external Apify calls)."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .apify_batch_intake import (stage_apify_file, process_next_apify_batch,
                                 begin_chunked_upload, append_chunked_upload,
                                 replay_apify_session, source_rows)
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

    def test_chunk_upload_is_idempotent_and_can_resume(self):
        session = begin_chunked_upload(
            filename='large.json', total=501, digest='a' * 64, user=self.user)
        rows = [{'id': str(i)} for i in range(500)]
        progress = append_chunked_upload(session_id=session.pk, batch=0,
                                         rows=rows, user=self.user)
        self.assertEqual(progress['uploaded_batches'], 1)
        retry = append_chunked_upload(session_id=session.pk, batch=0,
                                      rows=rows, user=self.user)
        self.assertEqual(retry['uploaded_batches'], 1)
        resumed = begin_chunked_upload(
            filename='large.json', total=501, digest='a' * 64, user=self.user)
        self.assertEqual(resumed.pk, session.pk)
        self.assertEqual(resumed.details['next_upload_batch'], 1)
        progress = append_chunked_upload(session_id=session.pk, batch=1,
                                         rows=[{'id': '500'}], user=self.user)
        self.assertEqual(progress['uploaded_batches'], 2)
        self.assertEqual(Activity.objects.filter(
            action='apify_upload_source_batch', details__session_id=session.pk).count(), 2)

    def test_completed_source_retained_and_replayable_without_apify(self):
        rows = [{'id': '123456789', 'ignored': {'unicode': 'Фаэтон', 'value': None}}]
        original = stage_apify_file(SimpleUploadedFile('saved.json', json.dumps(rows).encode()), self.user)
        original.details['buying_import_result'] = {'added': 1}
        original.save()
        with patch('procurement.apify_batch_intake.import_apify_results', return_value={'products': 1}) as importer:
            process_next_apify_batch(original.pk, self.user)
            archived = Activity.objects.get(action='apify_upload_source_batch', details__session_id=original.pk)
            self.assertNotIn('rows', archived.details)
            self.assertEqual(source_rows(archived), rows)
            replay = replay_apify_session(original.pk, self.user)
            self.assertNotEqual(replay.pk, original.pk)
            self.assertEqual(replay.details['replay_of'], original.pk)
            self.assertNotIn('buying_import_result', replay.details)
            process_next_apify_batch(replay.pk, self.user)
            self.assertEqual(importer.call_count, 2)
            self.assertEqual(json.loads(importer.call_args.args[0].read()), rows)
        other = get_user_model().objects.create_user('other_operator')
        with self.assertRaises(Activity.DoesNotExist):
            replay_apify_session(original.pk, other)

    def test_complete_file_late_cheapest_card_updates_once_and_trial_rolls_back(self):
        from decimal import Decimal
        from io import StringIO
        from django.core.management import call_command
        from .models import Supplier, Offer, Preferences, KaspiPriceAudit
        supplier = Supplier.objects.create(name='Phaeton DC')
        offer = Offer.objects.create(supplier=supplier, brand='MASUMA', article='ML-9108R',
            name='Стойка стабилизатора', cost=3940, stock='4')
        Preferences.objects.create(user=self.user, commission=Decimal('12.5'), expenses=250)
        def card(pk, article, price):
            return dict(id=pk, brand='MASUMA', title='MASUMA '+article,
                url='https://kaspi.kz/shop/p/part-'+pk+'/', offers=[dict(price=price)], city='Astana')
        rows = [card('177365263', 'ML-9108R', 9527)] * 500 + [card('129901708', 'ML9108R', 4590)]
        session = stage_apify_file(SimpleUploadedFile('full_phaeton.json', json.dumps(rows).encode()), self.user)
        output = StringIO()
        call_command('audit_apify_upload', session.pk, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['source_rows'], 501)
        self.assertEqual(report['new_matches'], 1)
        self.assertFalse(report['saved'])
        self.assertFalse(KaspiPriceAudit.objects.exists())
        process_next_apify_batch(session.pk, self.user)
        self.assertEqual(KaspiPriceAudit.objects.get(offer=offer).min_price, Decimal('9527'))
        self.assertTrue(process_next_apify_batch(session.pk, self.user)['done'])
        self.assertEqual(KaspiPriceAudit.objects.get(offer=offer).min_price, Decimal('4590'))
        self.assertEqual(KaspiPriceAudit.objects.count(), 1)
