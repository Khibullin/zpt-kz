"""The screen and workbook share the same filtered rows and valuation."""
import re
from decimal import Decimal
from io import BytesIO
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


def value_stock(item, field, basis):
    if item.filtered_stock is None or (field=='total' and item.partial_total and basis!='posted'):return None
    fields=['pp1','pp2'] if field=='total' else [field]
    amount=Decimal(0);unknown=False
    for attr in fields:
        qty=getattr(item,attr)
        if qty==0:continue
        if qty is None:
            if field!='total' or getattr(item,attr+'_present',False):unknown=True
            continue
        if attr=='kaspi' or basis=='snapshot' or not getattr(item,attr+'_posted',False) or item.valuation_error:
            unknown=True;continue
        lots=item.current_lots.get({'pp1':'PP1','pp2':'PP2'}[attr],[])
        if sum(l['quantity'] for l in lots)!=qty or any(l['unit_cost'] is None for l in lots):
            unknown=True;continue
        amount+=sum((Decimal(l['unit_cost'])*l['quantity'] for l in lots),Decimal(0))
    return None if unknown else amount


def balance_totals(rows,field,basis):
    groups=[]
    for unit,label in [('piece','шт.'),('set','компл.'),('pending','ед. (единица не подтверждена)')]:
        vals=[i.filtered_stock for i in rows if i.unit==unit and i.filtered_stock is not None]
        if vals:groups.append({'unit':label,'quantity':sum(vals)})
    for item in rows:item.stock_value=value_stock(item,field,basis)
    return {'groups':groups,'unknown':sum(i.filtered_stock is None or (field=='total' and i.partial_total and basis!='posted') for i in rows),
            'known_value':sum((i.stock_value for i in rows if i.stock_value is not None),Decimal(0)),
            'unknown_value':sum(i.stock_value is None for i in rows)}


def filter_labels(request):
    g=request.GET
    return [
        ('Склад',g.get('warehouse') or 'PP1 + PP2'),('Поставщик',g.get('supplier') or 'Все поставщики'),
        ('Бренд',g.get('brand') or 'Все бренды'),('Поиск',g.get('q') or 'Не задан'),
        ('Единица',{'piece':'Штуки','set':'Комплекты','pending':'Единица не подтверждена'}.get(g.get('unit'),'Все')),
        ('Остаток',{'positive':'В наличии','zero':'Нулевой','unknown':'Нет данных'}.get(g.get('stock_state'),'Все')),
        ('Привязка кодов',{'yes':'Коды привязаны','no':'Коды не привязаны'}.get(g.get('mapped'),'Все товары')),
        ('Источник остатка',{'posted':'По складскому учёту','snapshot':'По загруженному файлу'}.get(g.get('basis'),'Все источники')),
        ('Сортировка',{'-code':'Код по убыванию','name':'Название по возрастанию','-name':'Название по убыванию'}.get(g.get('sort'),'Код по возрастанию')),
        ('Сравнение с Kaspi','Да' if g.get('compare')=='1' else 'Нет')]


def export_balances(request,rows,totals):
    book=Workbook();sheet=book.active;sheet.title='Товары PP2' if request.GET.get('warehouse')=='PP2' else 'Товары по складам'
    summary=book.create_sheet('Сводка и фильтры')
    now=timezone.localtime()
    headers=['Артикул','Бренд','Товар','Поставщик','Единица']
    single=request.GET.get('warehouse') in ['PP1','PP2','Kaspi'] and request.GET.get('compare')!='1'
    if single:headers+=['Остаток '+request.GET['warehouse'],'Источник и дата остатка']
    else:
        headers+=['Остаток PP1','Источник и дата PP1','Остаток PP2','Источник и дата PP2','Известное количество PP1 + PP2']
        if request.GET.get('compare')=='1':headers+=['Остаток Kaspi','Источник и дата Kaspi','PP2 − Kaspi']
    headers+=['Закупочная стоимость остатка, ₸','В пути','Брак']
    sheet.append(headers);header_row=sheet.max_row
    for i in rows:
        data=[i.article or i.code,i.brand,i.name,i.supplier_label,i.get_unit_display()]
        if single:data += [i.filtered_stock if i.filtered_stock is not None else 'Нет данных',i.filtered_source]
        else:
            data += [i.pp1 if i.pp1 is not None else 'Нет данных',i.pp1_source,i.pp2 if i.pp2 is not None else 'Нет данных',i.pp2_source,i.total if i.total is not None else 'Нет данных']
            if request.GET.get('compare')=='1':data += [i.kaspi if i.kaspi is not None else 'Нет данных',i.kaspi_source,i.difference]
        data += [i.stock_value if i.stock_value is not None else 'Не определена',i.transit,i.damaged]
        sheet.append(data)
    for row in sheet:
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    summary['A1'].font=Font(size=16,bold=True,color='183451')
    summary['B2'].number_format='dd.mm.yyyy hh:mm'
    for cell in sheet[header_row]:
        cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='183451')
    summary.merge_cells(start_row=1,start_column=1,end_row=1,end_column=5)
    summary.row_dimensions[1].height=30
    for r in range(2,summary.max_row+1):
        if summary.cell(r,1).value is not None:
            summary.merge_cells(start_row=r,start_column=2,end_row=r,end_column=5)
            summary.row_dimensions[r].height=34
    summary.column_dimensions['A'].width=48
    for col in 'BCDE':summary.column_dimensions[col].width=22
    for row in summary:
        for cell in row:
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    sheet.row_dimensions[header_row].height=44
    sheet.freeze_panes=f'F{header_row+1}'
    sheet.auto_filter.ref=f'A{header_row}:{get_column_letter(len(headers))}{max(header_row,sheet.max_row)}'
    for n,header in enumerate(headers,1):
        sheet.column_dimensions[get_column_letter(n)].width=50 if header=='Товар' or 'Источник' in header else 28 if header=='Поставщик' else 22
    sheet.column_dimensions['A'].width=36
    for row in sheet.iter_rows(min_row=header_row+1):
        sheet.row_dimensions[row[0].row].height=42
        row[-3].number_format='#,##0.00'
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.print_title_rows=f'{header_row}:{header_row}'
    summary.append(['Остатки товаров по складам'])
    summary.append(['Дата выгрузки',now.replace(tzinfo=None)])
    for label,value in filter_labels(request):summary.append([label,value])
    summary.append(['Наименований',len(rows)])
    for group in totals['groups']:summary.append(['Количество, '+group['unit'],group['quantity']])
    if not rows:summary.append(['Количество, шт.',0])
    if totals['unknown']:summary.append(['Нет однозначных данных о количестве, товаров',totals['unknown']])
    summary.append(['Известная закупочная стоимость остатка, ₸' if totals['unknown_value'] else 'Закупочная стоимость остатка, ₸',totals['known_value']])
    summary.cell(summary.max_row,2).number_format='#,##0.00'
    if totals['unknown_value']:summary.append(['Стоимость не определена, товаров',totals['unknown_value']])
    summary.append(['Источник стоимости','Оставшиеся партии по складскому учёту. Цена Kaspi не является закупочной стоимостью.'])
    summary.append([])

    stream=BytesIO();book.save(stream)
    filename='Остатки_'+(request.GET.get('warehouse') or 'PP1+PP2')+'_'+(request.GET.get('supplier') or 'Все поставщики')+'_'+now.strftime('%d.%m.%Y')+'.xlsx'
    filename=re.sub(r'[\x00-\x1f/\\]+','-',filename)[:210]+('.xlsx' if len(filename)>210 else '')
    response=HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']=content_disposition_header(True,filename)
    return response
