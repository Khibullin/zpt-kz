from io import BytesIO
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile
from django.core.exceptions import ValidationError

ALIASES = {
    'article': {'артикул','номер детали','код товара','article'},
    'brand': {'бренд','производитель','торговая марка','brand'},
    'name': {'наименование','наименование товара','товар','название','name'},
    'packages': {'количество','кол-во','количество, шт.','кол-во, шт.','qty'},
    'cost': {'цена','цена, тг','цена, ₸','цена со скидкой','цена за единицу','price'},
}
def norm(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return ' '.join(str(v if v is not None else '').strip().split())

def parse_receipt(upload):
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError('Файл не должен превышать 10 МБ.')
    raw = upload.read()
    upload.seek(0)
    try:
        if Path(upload.name).suffix.lower() == '.xls':
            import xlrd
            book = xlrd.open_workbook(file_contents=raw, on_demand=True)
            try:
                sheet = book.sheet_by_index(0)
                if sheet.nrows > 20000 or sheet.ncols > 100:
                    raise ValueError('Слишком большая таблица')
                rows = [sheet.row_values(i) for i in range(sheet.nrows)]
            finally:
                book.release_resources()
        elif Path(upload.name).suffix.lower() == '.xlsx':
            from openpyxl import load_workbook
            with ZipFile(BytesIO(raw)) as z:
                if sum(x.file_size for x in z.infolist()) > 50 * 1024 * 1024:
                    raise ValueError('Слишком большой файл')
            book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
            try:
                sheet = book.worksheets[0]
                if (sheet.max_row or 0) > 20000 or (sheet.max_column or 0) > 100:
                    raise ValueError('Слишком большая таблица')
                rows = list(sheet.iter_rows(values_only=True))
            finally:
                book.close()
        else:
            raise ValueError('Выберите XLS или XLSX')
        for i, row in enumerate(rows):
            cols = {key: j for j,v in enumerate(row) for key,aliases in ALIASES.items() if norm(v).casefold() in aliases}
            if len(cols) == len(ALIASES):
                break
        else:
            raise ValueError('Не найдены колонки: бренд, артикул, наименование, количество, цена')
        result = []
        for index, row in enumerate(rows[i+1:], i+2):
            data = {key: norm(row[j]) if j < len(row) else '' for key,j in cols.items()}
            if not any(data.values()) or any(v.casefold() in ('итого','всего','итого:','всего:') for v in data.values()):
                continue
            if not all(data.values()):
                raise ValueError(f'Строка {index}: не заполнены все обязательные колонки')
            try:
                q = Decimal(data['packages'].replace(' ','').replace(',','.'))
                cost = Decimal(data['cost'].replace(' ','').replace(',','.'))
                if not q.is_finite() or not cost.is_finite() or q <= 0 or q != q.to_integral_value() or cost < 0 or cost != cost.quantize(Decimal('.01')):
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise ValueError(f'Строка {index}: проверьте количество и цену')
            data.update(packages=int(q), cost=cost, multiplier=1)
            result.append(data)
        if not result or len(result) > 300:
            raise ValueError('В накладной должно быть от 1 до 300 товарных строк')
        return result
    except Exception as exc:
        raise ValidationError(f'Не удалось разобрать накладную: {exc}') from exc
