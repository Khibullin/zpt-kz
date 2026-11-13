"""Private preparation material; publishing to Kaspi is a separate operation."""
from django.conf import settings
from django.db import models


class KaspiPreparedCard(models.Model):
    identity = models.CharField(max_length=64, unique=True)
    offer = models.ForeignKey('procurement.Offer', null=True, blank=True, on_delete=models.SET_NULL)
    brand = models.CharField(max_length=120)
    article = models.CharField(max_length=120)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    photo_url = models.URLField(max_length=2048, blank=True)
    category = models.CharField(max_length=512, blank=True)
    characteristics = models.JSONField(default=dict, blank=True)
    applicability = models.TextField(blank=True)
    applicability_source = models.URLField(max_length=2048, blank=True)
    verified = models.BooleanField(default=False)
    manual_fields = models.JSONField(default=list, blank=True)
    source = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class KaspiCardImage(models.Model):
    card = models.ForeignKey(KaspiPreparedCard, on_delete=models.CASCADE, related_name='images')
    digest = models.CharField(max_length=64)
    data = models.BinaryField()
    thumbnail = models.BinaryField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    source_url = models.URLField(max_length=2048, blank=True)
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'pk']
        unique_together = [('card', 'digest')]


class KaspiCardPrice(models.Model):
    card = models.ForeignKey(KaspiPreparedCard, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mode = models.CharField(max_length=12, choices=[('algorithm', 'По алгоритму'), ('manual', 'Вручную')], default='algorithm')
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('card', 'user')]


class KaspiPricingRule(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    method = models.CharField(max_length=24, choices=[
        ('minimum_minus_amount', 'Ниже минимальной цены на сумму'),
        ('minimum_minus_percent', 'Ниже минимальной цены на процент'),
        ('cost_plus_percent', 'Наценка к закупочной цене')], default='minimum_minus_amount')
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_profit = models.DecimalField(max_digits=12, decimal_places=2, default=1000)
    updated_at = models.DateTimeField(auto_now=True)
