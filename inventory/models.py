from django.conf import settings
from django.db import models

class ImportBatch(models.Model):
    kind = models.CharField(max_length=20)
    digest = models.CharField(max_length=64)
    filename = models.CharField(max_length=255)
    as_of = models.DateField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['kind', 'digest'], name='inventory_unique_import')]

class StockSnapshot(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT)
    warehouse = models.CharField(max_length=3, choices=[('PP1','PP1 · Asia Grade'),('PP2','PP2 · Rapida')])
    sku = models.CharField(max_length=120)
    name = models.CharField(max_length=500)
    brand = models.CharField(max_length=150, blank=True)
    quantity = models.PositiveIntegerField(null=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    # SKU proposals are deliberately not merged into physical products without mapping.
    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch','warehouse','sku'], name='inventory_snapshot_sku')]

class KaspiOperation(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64, unique=True)
    order_id = models.CharField(max_length=120)
    occurred_on = models.DateField()
    operation = models.CharField(max_length=100)
    product = models.CharField(max_length=500)
    revenue = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    commission = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pay_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    delivery = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    product_bonus = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    review_bonus = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_fees = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    source_row = models.JSONField(default=list)
    @property
    def after_fees(self):
        return (self.revenue or 0) + self.commission + self.pay_fee + self.delivery + self.product_bonus + self.review_bonus + self.other_fees

class Item(models.Model):
    code = models.CharField('Внутренний код', max_length=120, unique=True)
    name = models.CharField('Название', max_length=300)
    brand = models.CharField('Бренд', max_length=120, blank=True)
    article = models.CharField('Артикул', max_length=120, blank=True)
    unit = models.CharField('Единица учёта', max_length=15, choices=[('piece','Штука'),('set','Комплект')], default='piece')
    def __str__(self):
        return f'{self.code} · {self.name}'

class ItemAlias(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='aliases')
    source = models.CharField('Источник', max_length=15, choices=[('kaspi','SKU Kaspi'),('rapida','Штрихкод Rapida'),('pp1','Артикул PP1')])
    code = models.CharField('Код в источнике', max_length=120)
    multiplier = models.PositiveIntegerField('Единиц учёта в упаковке', default=1)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['source','code'], name='inventory_alias_unique'), models.CheckConstraint(condition=models.Q(multiplier__gte=1), name='inventory_alias_multiplier_positive')]

class StockDocument(models.Model):
    class Kind(models.TextChoices):
        OPENING='opening','Начальный остаток'
        RECEIPT='receipt','Приход от поставщика'
        TRANSFER='transfer','Перемещение'
        SALE='sale','Отгрузка покупателю'
        RETURN='return','Возврат покупателя'
    kind = models.CharField('Операция', max_length=15, choices=Kind.choices)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    warehouse = models.CharField('Склад', max_length=3, choices=[('PP1','PP1 · Asia Grade'),('PP2','PP2 · Rapida')])
    destination = models.CharField('Склад получения', max_length=3, blank=True, choices=[('PP1','PP1 · Asia Grade'),('PP2','PP2 · Rapida')])
    quantity = models.PositiveIntegerField('Количество')
    unit_cost = models.DecimalField('Себестоимость единицы, ₸', max_digits=14, decimal_places=2, null=True, blank=True)
    counterparty = models.CharField('Поставщик / основание', max_length=250, blank=True)
    external_number = models.CharField('Номер внешнего документа', max_length=120, blank=True)
    date = models.DateField('Дата документа')
    note = models.TextField('Комментарий', blank=True)
    status = models.CharField(max_length=15, choices=[('draft','Черновик'),('posted','Проведён'),('sent','Отправлен'),('partial','Принят частично'),('received','Принят полностью')], default='draft')
    received_quantity = models.PositiveIntegerField(default=0)
    related_sale = models.ForeignKey('self', verbose_name='Исходная отгрузка', on_delete=models.PROTECT, null=True, blank=True, related_name='returns')
    return_condition = models.CharField('Состояние возврата', max_length=15, blank=True, choices=[('good','Проверен, годен к продаже'),('damaged','Брак, исключить из продажи')])
    cost_allocations = models.JSONField(default=list, editable=False)
    total_cost = models.DecimalField(max_digits=16, decimal_places=2, null=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['item','external_number'],condition=models.Q(kind='sale') & ~models.Q(external_number=''), name='inventory_sale_order_item'),models.CheckConstraint(condition=models.Q(quantity__gte=1), name='inventory_document_positive'),models.CheckConstraint(condition=models.Q(received_quantity__lte=models.F('quantity')), name='inventory_received_limit'),models.CheckConstraint(condition=models.Q(unit_cost__isnull=True)|models.Q(unit_cost__gte=0), name='inventory_cost_nonnegative')]

class StockEntry(models.Model):
    document = models.ForeignKey(StockDocument, on_delete=models.PROTECT, related_name='entries')
    warehouse = models.CharField(max_length=7, choices=[('PP1','PP1'),('PP2','PP2'),('TRANSIT','В пути'),('DAMAGED','Брак')])
    quantity = models.IntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class StockEvent(models.Model):
    document = models.ForeignKey(StockDocument, on_delete=models.PROTECT, related_name='events')
    action = models.CharField(max_length=30)
    quantity = models.PositiveIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class MoneyAccount(models.Model):
    name = models.CharField('Счёт / касса (тенге)', max_length=150, unique=True)
    opening_balance = models.DecimalField('Начальная сумма, ₸', max_digits=14, decimal_places=2, default=0)
    opening_date = models.DateField('Дата начальной суммы')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    def __str__(self):
        return self.name

class CashEntry(models.Model):
    account = models.ForeignKey(MoneyAccount, on_delete=models.PROTECT)
    date = models.DateField('Дата')
    kind = models.CharField('Операция', max_length=20, choices=[('supplier','Оплата поставщику'),('kaspi','Выплата Kaspi'),('expense','Расход'),('owner_in','Внесение владельца'),('owner_out','Изъятие владельца')])
    amount = models.DecimalField('Сумма, ₸', max_digits=14, decimal_places=2)
    counterparty = models.CharField('Контрагент', max_length=250, blank=True)
    category = models.CharField('Статья', max_length=150, blank=True)
    stock_document = models.ForeignKey(StockDocument, on_delete=models.PROTECT, null=True, blank=True)
    reference = models.CharField('Номер платежа / основание', max_length=150)
    note = models.TextField('Комментарий', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='inventory_cash_positive'), models.UniqueConstraint(fields=['account','reference'], name='inventory_cash_reference')]
    @property
    def signed_amount(self):
        return self.amount if self.kind in ['kaspi','owner_in'] else -self.amount
