"""Read immutable staged JSON rows without materializing a whole price file."""
import json

from django.db import connection

from .models import PriceUpdate

CATEGORY_FIELDS = ('category', 'brand', 'article', 'cost', 'stock', '_conflict')


def iter_price_rows(batch_id, *, fields=(), chunk_size=128):
    yield from iter_json_rows(PriceUpdate, batch_id, fields=fields, chunk_size=chunk_size)


def iter_json_rows(model, batch_id, *, fields=(), chunk_size=128):
    """Stream an existing model JSON rows column, preserving source order."""
    if connection.vendor != 'postgresql':
        source = model.objects.only('rows').get(pk=batch_id)
        for row in source.rows:
            yield {field: row.get(field) for field in fields} if fields else row
        return
    table = connection.ops.quote_name(model._meta.db_table)
    column = connection.ops.quote_name(model._meta.get_field('rows').column)
    pk = connection.ops.quote_name(model._meta.pk.column)
    params = []
    if fields:
        projection = 'jsonb_build_object(' + ', '.join('%s::text, line.value -> %s::text' for _ in fields) + ')'
        for field in fields:
            params.extend((field, field))
    else:
        projection = 'line.value'
    params.append(batch_id)
    sql = (f'SELECT {projection} FROM {table} AS batch '
           f'CROSS JOIN LATERAL jsonb_array_elements(batch.{column}) WITH ORDINALITY AS line(value, row_number) '
           f'WHERE batch.{pk} = %s ORDER BY line.row_number')
    # Server-side cursor keeps the database driver from buffering the whole JSON.
    with connection.chunked_cursor() as cursor:
        cursor.execute(sql, params)
        while records := cursor.fetchmany(chunk_size):
            for (row,) in records:
                yield json.loads(row) if isinstance(row, str) else row
