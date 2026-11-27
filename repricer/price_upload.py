"""Prepare a native Kaspi price file on explicit staff request; never push prices."""
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException

from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from openpyxl import load_workbook

from .models import KaspiActiveExportRow
from .views import _latest_active_batch

HEADERS = ('SKU', 'model', 'brand', 'price', 'PP1', 'PP2', 'PP3', 'PP4', 'PP5', 'preorder')
SALT = 'repricer.kaspi-price-upload.v1'
MAX_BYTES = 15 * 1024 * 1024


def price_plan(batch):
    records = list(KaspiActiveExportRow.objects.filter(batch=batch).select_related('listing')) if batch else []
    counts = Counter(record.master_sku for record in records)
    plan = []
    for record in records:
        reason = ''
        if not record.master_sku or counts[record.master_sku] != 1:
            reason = 'Пустой или повторяющийся SKU в ACTIVE'
        elif record.listing_id and not record.listing.is_active:
            reason = 'Связанный товар ZPT.KZ неактивен'
        elif record.competitor_scan_status != 'MATCHED' or not record.competitor_min_price:
            reason = 'Нет подтверждённой цены конкурента'
        elif not record.quantity or not record.price:
            reason = 'Нет положительного остатка или текущей цены'
        elif record.competitor_min_price >= record.price:
            reason = 'Подтверждённый конкурент не дешевле нашей цены'
        proposed = record.competitor_min_price * 97 // 100 if record.competitor_min_price else None
        if not reason and not (1 <= proposed <= 999999999):
            reason = 'Расчётная цена вне допустимого диапазона'
        if not reason and proposed >= record.price:
            reason = 'Цена не требует снижения'
        plan.append({
            'id': record.pk, 'sku': record.master_sku, 'title': record.product_name,
            'current': record.price, 'minimum': record.competitor_min_price,
            'new': proposed, 'delta': proposed - record.price if proposed is not None and record.price else None,
            'checked_at': record.competitor_checked_at, 'reason': reason,
        })
    return plan


def plan_digest(batch, plan):
    raw = json.dumps([batch.pk if batch else None, plan], default=str, sort_keys=True).encode()
    return sha256(raw).hexdigest()


def update_price_file(upload, selected):
    if not upload or not upload.name.lower().endswith('.xlsx'):
        raise ValueError('Выберите прайс-лист Kaspi в формате XLSX: Управление товарами → Прайс-лист → Скачать в Excel.')
    if upload.size > MAX_BYTES:
        raise ValueError('Файл больше 15 МБ.')
    data = BytesIO(upload.read())
    try:
        with ZipFile(data) as archive:
            if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('Слишком большой распакованный файл Excel.')
        data.seek(0)
        workbook = load_workbook(data)
    except (BadZipFile, KeyError, OSError, ParseError, InvalidFileException) as exc:
        raise ValueError('Не удалось прочитать файл Excel.') from exc
    sheet = workbook.worksheets[0]
    headers = [cell.value for cell in sheet[1]]
    if headers != list(HEADERS):
        raise ValueError('Нужен прайс-лист Kaspi с колонками SKU, model, brand, price, PP1–PP5, preorder. Имя файла, включая ACTIVE.xlsx, не влияет на проверку.')
    by_sku = {}
    for cells in sheet.iter_rows(min_row=2):
        cell = cells[0]
        if cell.value is None:
            continue
        if cell.data_type == 'f':
            raise ValueError('SKU должен быть текстом или числом, без формулы.')
        sku = str(cell.value)
        if sku in by_sku:
            raise ValueError(f'В прайс-листе повторяется SKU {sku}.')
        by_sku[sku] = cells
    missing = [item['sku'] for item in selected if item['sku'] not in by_sku]
    if missing:
        raise ValueError('В прайс-листе не найдены выбранные SKU: ' + ', '.join(missing[:5]) + '. Цены не выгружены.')
    for item in selected:
        cells = by_sku[item['sku']]
        if not (1 <= item['new'] < item['current']):
            raise ValueError('Выгрузка разрешает только снижение положительной цены.')
        if cells[3].data_type == 'f' or cells[3].value != item['current']:
            raise ValueError(f"Цена SKU {item['sku']} в прайс-листе отличается от ACTIVE. Обновите ACTIVE и проверьте расчёт повторно.")
    for item in selected:
        by_sku[item['sku']][3].value = item['new']
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


@staff_member_required
@require_http_methods(['GET', 'POST'])
def prepare_kaspi_prices(request):
    batch = _latest_active_batch()
    plan = price_plan(batch)
    digest = plan_digest(batch, plan)
    eligible = [item for item in plan if not item['reason']]
    error = ''
    if request.method == 'POST':
        try:
            payload = signing.loads(request.POST.get('plan_token', ''), salt=SALT, max_age=3600)
            if payload != {'digest': digest, 'user': request.user.pk}:
                raise ValueError('Данные контроля цен изменились. Проверьте обновлённый расчёт и повторите скачивание.')
            ids = set(request.POST.getlist('selected'))
            selected = [item for item in eligible if str(item['id']) in ids]
            if not selected or ids != {str(item['id']) for item in selected}:
                raise ValueError('Выберите товары из доступного расчёта.')
            content = update_price_file(request.FILES.get('kaspi_template'), selected)
            response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
            response['Content-Disposition'] = f'attachment; filename="Kaspi_prices_{stamp}.xlsx"'
            response['Cache-Control'] = 'private, no-store'
            return response
        except signing.BadSignature:
            error = 'Расчёт устарел. Проверьте таблицу и повторите скачивание.'
        except ValueError as exc:
            error = str(exc)
    selected_ids = set(request.POST.getlist('selected')) if request.method == 'POST' else {str(item['id']) for item in eligible}
    for item in plan:
        item['selected'] = str(item['id']) in selected_ids
    return render(request, 'repricer/price_upload.html', {
        'active_batch': batch, 'plan': plan, 'eligible': eligible,
        'skipped': len(plan) - len(eligible), 'error': error,
        'plan_token': signing.dumps({'digest': digest, 'user': request.user.pk}, salt=SALT),
    }, status=400 if error else 200)
