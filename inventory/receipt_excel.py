from io import BytesIO
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
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

def receipt_unit(name, description='', omega=False, explicit=''):
    """Keep supplier quantities/prices in document units; never unpack sets."""
    text = norm(name + ' ' + description).casefold()
    explicit = norm(explicit).casefold().rstrip('.')
    piece = explicit in {'шт', 'штука', 'штук', 'piece', 'pcs'}
    kit = explicit in {'компл', 'комплект', 'набор', 'set', 'уп', 'упак', 'упаковка'}
    packed = re.search(r'\b1\s*уп(?:аковка|ак)?\.?\s*(?:по\s*)?(\d+)\s*шт', text)
    kit = kit or bool(packed) or bool(re.search(r'\b(?:комплект|набор)\b', text))
    if piece and kit:
        return '', 'Единица «штука» противоречит указанию комплекта / упаковки.'
    if kit:
        detail = f'Комплект: {packed[1]} шт. в упаковке.' if packed else 'Комплект указан в накладной.'
        return 'set', detail
    if piece:
        return 'piece', 'Штука указана в колонке единицы.'
    if explicit or re.search(r'\b(?:уп(?:ак(?:овка)?|аковки|аковок)?\.?|шт\.?|пара)\b', text):
        return '', 'Проверьте единицу и комплектность: неоднозначное обозначение в накладной.'
    if omega and name:
        return 'piece', 'Штука: поштучная позиция Omega без указания комплекта / упаковки.'
    return '', 'В накладной не указана единица: выберите штуку или комплект.'


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
        # Omega groups have the same header, a one-cell vehicle heading and
        # a subtotal outside the required columns. Only this known structure
        # enables group/footer recognition; incomplete products remain visible.
        omega = {'модель', 'oem', 'сумма со скидкой'}.issubset(
            {norm(value).casefold() for value in rows[i]})
        groups = set()
        if omega:
            for row in rows[i+1:]:
                for value in row:
                    match = re.fullmatch(r'Итого,\s*(.+):', norm(value), re.IGNORECASE)
                    if match:
                        groups.add(match[1].casefold())
        optional_cols = {}
        for j, value in enumerate(rows[i]):
            label = norm(value).casefold()
            if label == 'описание':
                optional_cols['description'] = j
            elif label in {'ед.', 'ед', 'ед. изм.', 'ед.изм.', 'единица', 'единица измерения'}:
                optional_cols['unit'] = j
        result, skipped = [], []
        for index, row in enumerate(rows[i+1:], i+2):
            cells = [norm(value) for value in row]
            nonempty = [value for value in cells if value]
            if not nonempty:
                continue
            data = {key: cells[j] if j < len(cells) else '' for key,j in cols.items()}
            repeated = all(data[key].casefold() in aliases for key, aliases in ALIASES.items())
            identity = any(data[key] for key in ('article', 'brand', 'name'))
            subtotal = not identity and any(
                re.fullmatch(r'(?:итого|всего)(?:\s*[:,].*)?', value, re.IGNORECASE)
                for value in nonempty)
            group = omega and len(nonempty) == 1 and nonempty[0].casefold() in groups
            footer = omega and not identity and any(
                re.fullmatch(r'(?:Количество:|Сумма всего:|Сумма заказа|'
                             r'Сальдо по контрагенту \(на .+\):|'
                             r'.+ — Предоплата|Итого к оплате:)', value, re.IGNORECASE)
                for value in nonempty)
            if repeated or subtotal or group or footer:
                reason = 'Заголовок таблицы' if repeated else 'Название группы' if group else 'Итог / служебная строка'
                skipped.append({'source_row': index, 'reason': reason, 'source_values': cells})
                continue
            errors = []
            missing = [key for key in ALIASES if not data[key]]
            if missing:
                labels = {'article': 'артикул', 'brand': 'бренд', 'name': 'название',
                          'packages': 'количество', 'cost': 'цена'}
                errors.append('Не заполнены: ' + ', '.join(labels[key] for key in missing))
            for key in ('packages', 'cost'):
                if not data[key]:
                    continue
                try:
                    value = Decimal(data[key].replace(' ', '').replace(',', '.'))
                    if key == 'packages':
                        valid = value.is_finite() and 0 < value <= 10000000 and value == value.to_integral_value()
                    else:
                        valid = value.is_finite() and 0 <= value < Decimal('1000000000000') and value == value.quantize(Decimal('.01'))
                    if not valid:
                        raise ValueError()
                    data[key] = int(value) if key == 'packages' else value
                except (InvalidOperation, ValueError):
                    errors.append('Проверьте ' + ('количество' if key == 'packages' else 'цену'))
            extra = {key: cells[j] if j < len(cells) else '' for key, j in optional_cols.items()}
            unit, basis = receipt_unit(data['name'], extra.get('description', ''), omega, extra.get('unit', ''))
            data.update(unit=unit, unit_basis=basis, multiplier=1, source_row=index, errors=errors, source_values=cells)
            if not unit:
                errors.append(basis)
            result.append(data)
        if not result or len(result) > 300:
            raise ValueError('В накладной должно быть от 1 до 300 товарных строк')
        totals = {}
        if omega:
            for row in rows[i+1:]:
                cells = [norm(value) for value in row]
                if 'Сумма всего:' in cells:
                    try:
                        declared = Decimal(cells[-1].replace(' ', '').replace(',', '.'))
                        if declared.is_finite() and declared >= 0:
                            totals['declared'] = declared
                    except InvalidOperation:
                        pass
        if all(isinstance(row['packages'], int) and isinstance(row['cost'], Decimal) for row in result):
            totals['calculated'] = sum(row['packages'] * row['cost'] for row in result)
            if 'declared' in totals:
                totals['difference'] = totals['calculated'] - totals['declared']
        return {'rows': result, 'skipped': skipped, 'totals': totals}
    except Exception as exc:
        raise ValidationError(f'Не удалось разобрать накладную: {exc}') from exc
