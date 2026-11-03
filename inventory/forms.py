from datetime import date
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
        fields=['kind','item','warehouse','destination','quantity','unit_cost','counterparty','external_number','date','related_sale','return_condition','note']
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
    def clean(self):
        data=super().clean()
        if data.get('quantity') is not None and data['quantity'] < 1: self.add_error('quantity','Минимум 1')
        if data.get('unit_cost') is not None and data['unit_cost'] < 0: self.add_error('unit_cost','Не меньше нуля')
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
    class Meta:
        model=CashEntry
        fields=['account','date','kind','amount','counterparty','category','stock_document','reference','note']
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
