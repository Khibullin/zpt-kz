"""Read immutable staged JSON rows without materializing a whole price file."""
import json
import base64
import zlib

PRICE_CHUNK_SIZE = 500
COMPRESSED_FORMAT = 'zpt-price-rows-zlib-v1'


def encode_price_rows(rows):
    """Keep PostgreSQL JSON parsing bounded on its 256 MiB instance."""
    return {'format': COMPRESSED_FORMAT, 'chunks': [
        base64.b64encode(zlib.compress(json.dumps(
            rows[start:start + PRICE_CHUNK_SIZE], ensure_ascii=False,
            separators=(',', ':')).encode('utf-8'))).decode('ascii')
        for start in range(0, len(rows), PRICE_CHUNK_SIZE)]}


def stored_rows_slice(stored, start=0, stop=None):
    if isinstance(stored, list):
        yield from stored[start:stop]
        return
    if not isinstance(stored, dict) or stored.get('format') != COMPRESSED_FORMAT:
        raise ValueError('Неизвестный формат сохранённого прайса.')
    for number, chunk in enumerate(stored['chunks']):
        offset = number * PRICE_CHUNK_SIZE
        if offset + PRICE_CHUNK_SIZE <= start:
            continue
        if stop is not None and offset >= stop:
            break
        rows = json.loads(zlib.decompress(base64.b64decode(chunk)))
        yield from rows[max(0, start-offset):None if stop is None else max(0, stop-offset)]


from django.db import connection

from .models import PriceUpdate

CATEGORY_FIELDS = ('category', 'brand', 'article', 'cost', 'stock', '_conflict')


def iter_price_rows(batch_id, *, fields=(), chunk_size=128):
    # Small scalar probe avoids loading legacy Omega JSON into the web worker.
    encoding = PriceUpdate.objects.filter(pk=batch_id).values_list('rows__format', flat=True).first()
    if encoding == COMPRESSED_FORMAT:
        source = PriceUpdate.objects.only('rows').get(pk=batch_id)
        for row in stored_rows_slice(source.rows):
            yield {field: row.get(field) for field in fields} if fields else row
    else:
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
