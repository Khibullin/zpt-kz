import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, TestCase
from django.core.cache import cache
from django.utils import timezone
from django.contrib.auth import get_user_model
from procurement.models import Supplier, PriceUpdate
from procurement.price_source import CATEGORY_FIELDS, iter_price_rows
from procurement.omega_selection_requests import price_categories, pick_rows


class StagedPriceSourceTests(TestCase):
    def setUp(self):
        cache.clear()
        user=get_user_model().objects.create_user('source-audit')
        supplier=Supplier.objects.create(name='Omega')
        self.rows=[
            {'category':'Колодки','brand':'EEP','article':'1','cost':'2000','stock':'20>','description':'large '*1000},
            {'category':'Колодки','brand':'EEP','article':'2','cost':'1999','stock':'1'},
            {'category':'Колодки','brand':'EEP','article':'3','cost':'5000','stock':'0'},
            {'category':'Колодки','brand':'EEP','article':'4','cost':'5000','stock':'1','_conflict':True},
            {'category':'Колодки','brand':'EEP','article':'5','cost':'5000','stock':'1','_conflict':False},
        ]
        self.batch=PriceUpdate.objects.create(user=user,supplier=supplier,filename='audit.csv',checked_on=timezone.localdate(),sha256='audit-source',rows=self.rows)

    def test_projection_preserves_eligibility_and_original_rows(self):
        projected=list(iter_price_rows(self.batch.pk,fields=CATEGORY_FIELDS))
        self.assertNotIn('description',projected[0])
        result=price_categories(self.batch)
        self.assertEqual([(r['total'],r['in_stock'],r['eligible']) for r in result],[(5,4,2)])
        self.assertEqual(list(iter_price_rows(self.batch.pk)),self.rows)
        selected,_=pick_rows(self.batch,['колодки'],Decimal('2000'),20)
        self.assertEqual([r['article'] for r in selected],['1','5'])
        self.assertEqual(selected[0]['description'],self.rows[0]['description'])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.rows,self.rows)


class PostgresStreamingTests(SimpleTestCase):
    def test_json_rows_are_read_in_bounded_cursor_batches_with_typed_values(self):
        cursor=MagicMock()
        cursor.__enter__.return_value=cursor
        cursor.fetchmany.side_effect=[[(json.dumps({'_conflict':False,'brand':'EEP'}),)],[(json.dumps({'_conflict':True,'brand':'EEP'}),)],[]]
        connection=MagicMock(vendor='postgresql')
        connection.ops.quote_name.side_effect=lambda name:'"'+name+'"'
        connection.chunked_cursor.return_value=cursor
        with patch('procurement.price_source.connection',connection):
            rows=list(iter_price_rows(42,fields=CATEGORY_FIELDS,chunk_size=2))
        self.assertIs(rows[0]['_conflict'],False)
        self.assertIs(rows[1]['_conflict'],True)
        self.assertEqual(cursor.fetchmany.call_args.args,(2,))
        sql,params=cursor.execute.call_args.args
        self.assertIn('jsonb_array_elements',sql)
        self.assertEqual(params[-1],42)
        self.assertNotIn('description',sql)
