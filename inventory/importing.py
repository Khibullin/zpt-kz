import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile
from openpyxl import load_workbook
from django.db import transaction
from .models import ImportBatch, StockSnapshot, KaspiOperation

def money(value, nullable=False):
    if value is None or value == '':
        return None if nullable else Decimal('0')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(Decimal('.01')):
            raise ValueError('Некорректная денежная сумма')
        return number
    except (InvalidOperation, TypeError):
        raise ValueError('Некорректная денежная сумма')

def parse_file(raw, kind):
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('Максимальный размер файла — 10 МБ')
    with ZipFile(BytesIO(raw)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError('Слишком большой распакованный файл')
    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        if sheet.max_row > 20000:
            raise ValueError('Не более 20 000 строк за загрузку')
        rows = list(sheet.iter_rows(values_only=True))
        result = []
        if kind == 'stock':
            if list(rows[0][:6]) != ['SKU','model','brand','price','PP1','PP2']:
                raise ValueError('Ожидается выгрузка остатков Kaspi с колонками SKU, model, brand, price, PP1, PP2')
            seen = set()
            for r in rows[1:]:
                if not r[0]:
                    continue
                sku = str(r[0]).strip()
                if sku in seen:
                    raise ValueError(f'Повтор SKU: {sku}')
                seen.add(sku)
                q = Decimal(str(r[5]))
                if not q.is_finite() or q < 0 or q != q.to_integral_value():
                    raise ValueError(f'Неверное количество PP2: {sku}')
                result.append(dict(sku=sku, name=str(r[1] or ''), brand=str(r[2] or ''), quantity=int(q), price=money(r[3], True)))
        elif kind == 'pp1':
            header = next((i for i,r in enumerate(rows) if 'PP1 после, шт.' in r and 'Артикул' in r), None)
            if header is None:
                raise ValueError('Ожидается файл остатков PP1 после перемещения')
            columns = {v:i for i,v in enumerate(rows[header]) if v}
            seen = set()
            for r in rows[header+1:]:
                if not isinstance(r[0], int) or not r[columns['Артикул']]:
                    continue
                sku = str(r[columns['Артикул']]).strip()
                if sku in seen:
                    raise ValueError(f'Повтор артикула: {sku}')
                seen.add(sku)
                raw_q = r[columns['PP1 после, шт.']]
                q = None if raw_q is None else Decimal(str(raw_q))
                if q is not None and (not q.is_finite() or q < 0 or q != q.to_integral_value()):
                    raise ValueError(f'Неверное количество PP1: {sku}')
                result.append(dict(sku=sku,name=sku,brand='',quantity=None if q is None else int(q),price=None))
        elif kind == 'sales':
            header = next((i for i,r in enumerate(rows) if 'Номер заказа (ID/RRN)' in r), None)
            if header is None:
                raise ValueError('Не найдены колонки отчёта операций Kaspi')
            columns = {v:i for i,v in enumerate(rows[header]) if v}
            def value(r, label):
                if label not in columns:
                    raise ValueError(f'Нет колонки: {label}')
                return r[columns[label]]
            occurrences = Counter()
            for r in rows[header+1:]:
                order = value(r, 'Номер заказа (ID/RRN)')
                if not order:
                    continue
                canonical = json.dumps([str(v) if v is not None else None for v in r], ensure_ascii=False)
                # Ignore Excel sequence number; retain repeated identical operations by occurrence.
                canonical = json.dumps(json.loads(canonical)[1:], ensure_ascii=False)
                occurrences[canonical] += 1
                fingerprint = hashlib.sha256((canonical + ':' + str(occurrences[canonical])).encode()).hexdigest()
                result.append(dict(fingerprint=fingerprint, order_id=str(order), occurred_on=datetime.strptime(str(value(r,'Дата операции')), '%d.%m.%Y').date(), operation=str(value(r,'Тип операции')), product=str(value(r,'Детали покупки') or ''), revenue=money(value(r,'Сумма операции (т)'),True), commission=money(value(r,'Комиссия за операции (т)')), pay_fee=money(value(r,'Комиссия Kaspi Pay (т)')), delivery=money(value(r,'Стоимость услуги за Kaspi Доставку')), product_bonus=money(value(r,'Оплата услуг за акцию Бонусы на товар')), review_bonus=money(value(r,'Оплата услуг за акцию Бонусы за отзыв')), other_fees=sum((money(value(r,k)) for k in ['Комиссия за операции по карте (т)','Комиссия за обеспечение платежа (т)','Комиссия Kaspi Travel (т)']), Decimal('0')), source_row=json.loads(canonical)))
        else:
            raise ValueError('Неизвестный формат')
        if not result:
            raise ValueError('В файле нет данных')
        return result
    finally:
        workbook.close()

@transaction.atomic
def import_file(raw, kind, filename, as_of, user):
    parsed = parse_file(raw, kind)
    batch, created = ImportBatch.objects.get_or_create(kind=kind, digest=hashlib.sha256(raw).hexdigest(), defaults=dict(filename=filename[:255], as_of=as_of, created_by=user))
    if not created:
        return batch, 0
    count = 0
    for data in parsed:
        if kind in ['stock','pp1']:
            StockSnapshot.objects.create(batch=batch, warehouse='PP1' if kind == 'pp1' else 'PP2', **data)
            count += 1
        else:
            _, created = KaspiOperation.objects.get_or_create(fingerprint=data['fingerprint'], defaults=dict(batch=batch, **{k:v for k,v in data.items() if k != 'fingerprint'}))
            count += int(created)
    return batch, count
