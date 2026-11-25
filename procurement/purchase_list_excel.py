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
    'name': 'ИП «КВАНТ»', 'bin': '600710301320',
    'address': 'Казахстан, Алматы, ул. Навои, дом 58, кв./офис 86',
    'phone': '+7 701 191 00 00', 'email': 'rkhaibullin@gmail.com', 'site': 'https://zpt.kz',
    'bank': 'АО «Kaspi Bank»', 'bik': 'CASPKZKA',
    'account': 'KZ07722S000001183310', 'kbe': '19',
    'delivery_address': 'Алматы, микрорайон Атырау, 159/8, восьмой ангар',
    'delivery_contact': 'Рашид Хайбуллин',
    'delivery_role': 'менеджер',
    'delivery_phone': '+7 706 600 3112',
}


class PurchaseListExportForm(forms.Form):
    buyer = forms.CharField(label='Закупщик', max_length=200, required=False)


def organization_details():
    return {**ORGANIZATION, **getattr(settings, 'PROCUREMENT_BUYER_DETAILS', {})}


def delivery_details_text(organization=None):
    details = organization if organization is not None else organization_details()
    return (f'Адрес доставки: {details["delivery_address"]}\n'
            f'Контактное лицо: {details["delivery_role"]} {details["delivery_contact"]} · '
            f'Телефон: {details["delivery_phone"]}')


def purchaser_name(user):
    return 'Рашид Хайбуллин'


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
    merged(3, f'Покупатель: {organization["name"]} · ИИН: {organization["bin"]}')
    merged(4, f'Закупщик: {buyer} · Менеджер по закупкам')
    merged(5, f'Адрес: {organization["address"]}', 30)
    merged(6, f'Телефон: {organization["phone"]} · Email: {organization["email"]}', 25)
    merged(7, f'Банк: {organization["bank"]} · БИК: {organization["bik"]} · ИИК: {organization["account"]} · КБе: {organization["kbe"]}', 30)
    merged(8, f'Позиций: {len(selections)} · Валюта: казахстанский тенге (₸) · Сайт: {organization["site"]}')
    merged(9, delivery_details_text(organization), 40)
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

    # Separate printable supplier order sheets, while preserving the overall summary sheet.
    used_titles = {sheet.title}
    supplier_lines = defaultdict(list)
    for selection in selections:
        supplier_lines[selection.offer.supplier.name].append(selection)
    for supplier, lines in sorted(supplier_lines.items()):
        title_base = ''.join('_' if symbol in '[]:*?/\\\\' else symbol for symbol in supplier).strip()[:31] or 'Поставщик'
        title = title_base
        suffix = 2
        while title in used_titles:
            ending = f' {suffix}'
            title = title_base[:31 - len(ending)] + ending
            suffix += 1
        used_titles.add(title)
        tab = workbook.create_sheet(title)
        tab.merge_cells('A1:I1')
        tab['A1'] = f'ЗАКАЗ ПОСТАВЩИКУ · {supplier}'
        tab['A1'].font = Font(name='Arial', size=17, bold=True, color='FFFFFF')
        tab['A1'].fill = PatternFill('solid', fgColor=navy)
        tab.row_dimensions[1].height = 35
        for index, value in enumerate((
            f'Дата: {timezone.localtime():%d.%m.%Y %H:%M} · Планируемая закупка',
            f'Покупатель: {organization["name"]} · ИИН: {organization["bin"]}',
            f'Закупщик: {buyer} · Менеджер по закупкам',
            f'Адрес: {organization["address"]}',
            f'Телефон: {organization["phone"]} · Email: {organization["email"]}',
            f'Банк: {organization["bank"]} · БИК: {organization["bik"]} · ИИК: {organization["account"]} · КБе: {organization["kbe"]}',
            f'Сайт: {organization["site"]}',
        ), 2):
            tab.merge_cells(start_row=index, start_column=1, end_row=index, end_column=9)
            cell = tab.cell(index, 1, str(value))
            cell.data_type = 's'
            cell.alignment = Alignment(wrap_text=True, vertical='center')
            tab.row_dimensions[index].height = 30 if index in (5, 7) else 22
        tab.merge_cells('A9:I9')
        cell = tab.cell(9, 1, delivery_details_text(organization))
        cell.data_type = 's'
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        tab.row_dimensions[9].height = 40
        for col, label in enumerate(headers, 1):
            cell = tab.cell(10, col, label)
            cell.font = Font(name='Arial', bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor=teal)
        supplier_sum = Decimal('0')
        for idx, selection in enumerate(lines, 1):
            offer = selection.offer
            row_no = 10 + idx
            subtotal = offer.cost * selection.quantity
            supplier_sum += subtotal
            values = (idx, supplier, offer.brand, offer.article, offer.name, offer_unit(offer), selection.quantity, offer.cost, subtotal)
            for col, value in enumerate(values, 1):
                cell = tab.cell(row_no, col, value if col in (1, 7, 8, 9) else str(value))
                if col in (2, 3, 4, 5, 6):
                    cell.data_type = 's'
                cell.border = Border(bottom=edge)
                cell.alignment = Alignment(wrap_text=col <= 6, vertical='center')
                if idx % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F3F7FB')
                if col in (8, 9):
                    cell.number_format = '#,##0.00'
            tab.row_dimensions[row_no].height = 32
        total_row = 12 + len(lines)
        tab.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=8)
        tab.cell(total_row, 1, 'ИТОГО ПО ПОСТАВЩИКУ')
        tab.cell(total_row, 9, supplier_sum).number_format = '#,##0.00'
        for cell in tab[total_row]:
            cell.font = Font(name='Arial', size=12, bold=True, color=navy)
            cell.fill = PatternFill('solid', fgColor='EAF8F4')
        for col, width in enumerate([5, 20, 16, 24, 52, 10, 13, 18, 20], 1):
            tab.column_dimensions[get_column_letter(col)].width = width
        tab.freeze_panes = 'E11'
        tab.auto_filter.ref = f'A10:I{10 + len(lines)}'
        tab.print_title_rows = '1:10'
        tab.sheet_properties.pageSetUpPr.fitToPage = True
        tab.page_setup.orientation = 'landscape'
        tab.page_setup.fitToWidth = 1
        tab.page_setup.fitToHeight = 0
        tab.print_area = f'A1:I{total_row}'
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
