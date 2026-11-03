from datetime import date
from decimal import Decimal
from django import forms
from .models import Item, ItemAlias, StockDocument, MoneyAccount, CashEntry

class ItemForm(forms.ModelForm):
    class Meta:
        model=Item
        fields=['code','name','brand','article','unit']

class AliasForm(forms.ModelForm):
    class Meta:
        model=ItemAlias
        fields=['item','source','code','multiplier']

    def clean_item(self):
        item=self.cleaned_data['item']
        if item.unit=='pending':raise forms.ValidationError('Сначала подтвердите единицу учёта товара при сопоставлении')
        return item

class DocumentForm(forms.ModelForm):
    class Meta:
        model=StockDocument
        fields=['kind','item','warehouse','destination','quantity','unit_cost','counterparty','external_number','date','related_sale','return_condition','sale_amount','selling_costs','note']
        labels={'item':'Товар','account':'Счёт','stock_document':'Приход поставщика'}
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['related_sale'].queryset=StockDocument.objects.filter(kind='sale',status='posted').select_related('item')
        self.fields['sale_amount'].help_text='Общая сумма документа, не цена единицы. Не заполняйте догадкой.'
        self.fields['selling_costs'].help_text='Общие расходы этой продажи; при возврате — только фактически возвращённые расходы.'

    def clean(self):
        data=super().clean()
        for key in ['sale_amount','selling_costs']:
            if data.get(key) is not None and data[key] < 0:self.add_error(key,'Не меньше нуля')
        if data.get('quantity') is not None and data['quantity'] < 1: self.add_error('quantity','Минимум 1')
        if data.get('unit_cost') is not None and data['unit_cost'] < 0: self.add_error('unit_cost','Не меньше нуля')
        if data.get('kind') in ['adjustment','writeoff'] and not data.get('note'):self.add_error('note','Укажите причину корректировки')
        if data.get('kind') == 'transfer' and (not data.get('destination') or data.get('destination') == data.get('warehouse')):
            self.add_error('destination','Выберите другой склад')
        if data.get('kind') == 'receipt':
            if data.get('unit_cost') is None: self.add_error('unit_cost','Укажите фактическую себестоимость')
            if not data.get('counterparty'): self.add_error('counterparty','Укажите поставщика')
        if data.get('kind') == 'sale':
            if data.get('warehouse') != 'PP2': self.add_error('warehouse','Продажи выполняются только с PP2')
            if not data.get('external_number'): self.add_error('external_number','Укажите номер заказа Kaspi')
        if data.get('kind') == 'return':
            if not data.get('related_sale'): self.add_error('related_sale','Выберите исходную отгрузку')
            if not data.get('return_condition'): self.add_error('return_condition','Укажите результат проверки возврата')
        return data

class AccountForm(forms.ModelForm):
    class Meta:
        model=MoneyAccount
        fields=['name','opening_balance','opening_date']
        widgets={'opening_date':forms.DateInput(attrs={'type':'date'})}

class CashForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['kind'].choices=[c for c in self.fields['kind'].choices if c[0] not in ['transfer_in','transfer_out','supplier_refund']]
        self.fields['stock_document'].queryset=StockDocument.objects.filter(kind='receipt',status='posted').select_related('item')

    class Meta:
        model=CashEntry
        fields=['account','date','kind','amount','counterparty','category','stock_document','reference','note']
        labels={'item':'Товар','account':'Счёт','stock_document':'Приход поставщика'}
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        data=super().clean()
        if data.get('amount') is not None and data['amount'] <= 0: self.add_error('amount','Сумма должна быть больше нуля')
        account=data.get('account')
        if account and data.get('date') and data['date'] < account.opening_date:
            self.add_error('date','Платёж раньше даты начального остатка счёта')
        doc=data.get('stock_document')
        if doc and (data.get('kind') != 'supplier' or doc.kind != 'receipt' or doc.status != 'posted'):
            self.add_error('stock_document','Связать можно оплату поставщику с проведённым приходом')
        return data


class MoneyTransferForm(forms.Form):
    source = forms.ModelChoiceField(label='Со счёта', queryset=MoneyAccount.objects.all())
    destination = forms.ModelChoiceField(label='На счёт', queryset=MoneyAccount.objects.all())
    date = forms.DateField(label='Дата', initial=date.today, widget=forms.DateInput(attrs={'type':'date'}))
    amount = forms.DecimalField(label='Сумма, ₸', max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    reference = forms.CharField(label='Номер платежа / основание', max_length=150)
    note = forms.CharField(label='Комментарий', required=False, widget=forms.Textarea)
    def clean(self):
        data=super().clean()
        if data.get('source') and data.get('destination'):
            if data['source']==data['destination']:self.add_error('destination','Выберите другой счёт')
            if data.get('date') and any(data['date'] < a.opening_date for a in [data['source'],data['destination']]):self.add_error('date','Дата раньше начального остатка счёта')
        return data

class OpeningForm(forms.Form):
    from .models import ImportBatch
    batch = forms.ModelChoiceField(label='Файл остатков', queryset=ImportBatch.objects.filter(kind__in=['pp1','stock']).order_by('-as_of','-pk'))
    date = forms.DateField(label='Дата начального остатка', initial=date.today, widget=forms.DateInput(attrs={'type':'date'}))
    checked = forms.BooleanField(label='Создать только черновики; количества и себестоимость проверю перед проведением')

class FinancialDetailsForm(forms.ModelForm):
    class Meta:
        model=StockDocument
        fields=['sale_amount','selling_costs']
    reason=forms.CharField(label='Основание уточнения',widget=forms.Textarea)
    def clean(self):
        data=super().clean()
        for key in ['sale_amount','selling_costs']:
            if data.get(key) is not None and data[key]<0:self.add_error(key,'Не меньше нуля')
        return data

class BankCheckForm(forms.Form):
    account=forms.ModelChoiceField(label='Счёт',queryset=MoneyAccount.objects.all())
    date=forms.DateField(label='На конец дня',initial=date.today,widget=forms.DateInput(attrs={'type':'date'}))
    actual=forms.DecimalField(label='Остаток по выписке, ₸',max_digits=14,decimal_places=2)
    def clean(self):
        data=super().clean()
        if data.get('account') and data.get('date') and data['date']<data['account'].opening_date:self.add_error('date','Дата раньше начального остатка счёта')
        return data
