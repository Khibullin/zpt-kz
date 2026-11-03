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
