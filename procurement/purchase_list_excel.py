"""Read-only, supplier-separated XLSX for a user's planned purchases."""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from math import ceil

from django import forms
from django.conf import settings
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


# Existing operator details and business contact from the ZPT.KZ contact pages.
ORGANIZATION = {
    'name': 'ТОО «Азия Грейт»', 'bin': '120140021357',
    'address': 'Казахстан, г. Алматы, Бостандыкский район, мкр Нур Алатау, ул. Мурат, дом 94',
    'phone': '+7 771 360 7040', 'email': 'rkhaibullin@gmail.com', 'site': 'https://zpt.kz',
}


class PurchaseListExportForm(forms.Form):
    buyer = forms.CharField(label='Закупщик', max_length=200, required=False)


def organization_details():
    return {**ORGANIZATION, **getattr(settings, 'PROCUREMENT_BUYER_DETAILS', {})}


def purchaser_name(user):
    # The owner's existing admin account has no first/last name configured.
    owner = 'Ринат Хайбуллин' if user.is_superuser and user.username == 'admin' else ''
    return user.get_full_name().strip() or owner or user.get_username()


def offer_unit(offer):
    data = offer.characteristics or {}
    value = data.get('unit') or data.get('unit_name')
    return value.strip()[:40] if isinstance(value, str) and value.strip() else 'ед.'


def export_purchase_list(selections, buyer):
    """No order, reservation, stock or accounting write is made here."""
    organization = organization_details()
    selections = list(selections)
    workbook = Workbook()
    workbook.properties.creator = 'ZPT.KZ'
    workbook.properties.title = 'Список закупки ZPT.KZ'
    workbook.properties.subject = 'Планируемая закупка'
    sheet = workbook.active
    sheet.title = 'Список закупки'
    navy = '142E4D'
    teal = '087E88'
    edge = Side(style='thin', color='DBE4EE')

    def text(row, column, value):
        cell = sheet.cell(row, column, str(value))
        cell.data_type = 's'  # Articles retain leading zeros; text is never a formula.
        return cell

    def merged(row, value, height=22):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=9)
        cell = text(row, 1, value)
        cell.font = Font(name='Arial', size=11, color=navy)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        sheet.row_dimensions[row].height = height
        return cell

    title = merged(1, 'СПИСОК ЗАКУПКИ · ZPT.KZ', 34)
    title.font = Font(name='Arial', size=18, bold=True, color='FFFFFF')
    title.fill = PatternFill('solid', fgColor=navy)
    merged(2, f'Дата: {timezone.localtime():%d.%m.%Y %H:%M} · Планируемая закупка')
    merged(3, f'Покупатель: {organization["name"]} · БИН: {organization["bin"]}')
    merged(4, f'Закупщик: {buyer}')
    merged(5, f'Адрес: {organization["address"]}', 30)
    merged(6, f'Телефон: {organization["phone"]} · Email: {organization["email"]}', 25)
    merged(7, f'Сайт: {organization["site"]}')
    merged(8, f'Товарных позиций: {len(selections)} · Валюта: казахстанский тенге (₸)')
    headers = ['№', 'Поставщик', 'Бренд', 'Артикул', 'Наименование', 'Ед.', 'Количество', 'Цена, ₸', 'Сумма, ₸']
    for column, label in enumerate(headers, 1):
        cell = text(10, column, label)
        cell.font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=teal)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[10].height = 30
    totals = defaultdict(lambda: Decimal('0'))
    for index, selection in enumerate(selections, 1):
        row = index + 10
        offer = selection.offer
        total = offer.cost * selection.quantity
        totals[offer.supplier.name] += total
        sheet.cell(row, 1, index)
        for column, value in [(2, offer.supplier.name), (3, offer.brand),
                              (4, offer.article), (5, offer.name), (6, offer_unit(offer))]:
            text(row, column, value)
        sheet.cell(row, 7, selection.quantity)
        sheet.cell(row, 8, offer.cost)
        sheet.cell(row, 9, total)
        lines = max(ceil(len(value) / width) for value, width in [
            (offer.supplier.name, 18), (offer.brand, 14), (offer.article, 22),
            (offer.name, 42), (offer_unit(offer), 8)])
        sheet.row_dimensions[row].height = min(409.5, max(32, 15 * lines))
        for column in range(1, 10):
            cell = sheet.cell(row, column)
            cell.font = Font(name='Arial', size=11, color=navy)
            cell.border = Border(bottom=edge)
            cell.alignment = Alignment(wrap_text=column <= 6, vertical='center')
            if index % 2 == 0:
                cell.fill = PatternFill('solid', fgColor='F3F7FB')
            if column in (8, 9):
                cell.number_format = '#,##0.00'
    last = 10 + len(selections)
    line = last + 2
    for supplier, total in sorted(totals.items()):
        sheet.merge_cells(start_row=line, start_column=1, end_row=line, end_column=8)
        text(line, 1, f'Итого · {supplier}')
        sheet.cell(line, 9, total).number_format = '#,##0.00'
        line += 1
    sheet.merge_cells(start_row=line, start_column=1, end_row=line, end_column=8)
    text(line, 1, 'ОБЩАЯ СУММА ЗАКУПКИ')
    sheet.cell(line, 9, sum(totals.values(), Decimal('0'))).number_format = '#,##0.00'
    sheet.row_dimensions[line].height = 28
    for cell in sheet[line]:
        cell.font = Font(name='Arial', size=12, bold=True, color=navy)
        cell.fill = PatternFill('solid', fgColor='EAF8F4')
    merged(line + 2, 'Расчёт по прайсам поставщиков. Окончательные цены и наличие подтверждает поставщик.', 28)
    for column, width in enumerate([5, 20, 16, 24, 52, 10, 13, 18, 20], 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = 'E11'
    sheet.auto_filter.ref = f'A10:I{max(10, last)}'
    sheet.print_title_rows = '1:10'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_options.horizontalCentered = True
    sheet.print_area = f'A1:I{line + 2}'
    sheet.oddFooter.left.text = 'ZPT.KZ · Список закупки'
    sheet.oddFooter.right.text = 'Страница &P из &N'
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
