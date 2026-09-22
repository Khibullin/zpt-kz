from django.conf import settings
from django.db import models

from orders.models import Order

from .config import CURRENCY_KZT, HASH_ALGO_SHA256, INVID_MAX, INVID_MIN, MODE_TEST


class PaymentAttempt(models.Model):
    MODE_TEST = MODE_TEST
    STATUS_CREATED = 'created'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_CHOICES = [
        (STATUS_CREATED, 'Создана'),
        (STATUS_CONFIRMED, 'Подтверждена Result URL'),
    ]

    inv_id = models.AutoField(primary_key=True, verbose_name='InvId')
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name='payment_attempts',
        verbose_name='Заказ',
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Сумма снимка',
    )
    currency = models.CharField(
        max_length=3,
        default=CURRENCY_KZT,
        verbose_name='Валюта',
    )
    mode = models.CharField(
        max_length=8,
        default=MODE_TEST,
        db_index=True,
        verbose_name='Режим',
    )
    hash_algo = models.CharField(
        max_length=16,
        default=HASH_ALGO_SHA256,
        verbose_name='Алгоритм подписи',
    )
    merchant_login = models.CharField(
        max_length=64,
        verbose_name='MerchantLogin',
    )
    seller_profile_id = models.PositiveIntegerField(
        verbose_name='ID продавца (снимок)',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='robokassa_payment_attempts',
        verbose_name='Создал',
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_CREATED,
        db_index=True,
        verbose_name='Статус попытки',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создана')
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Подтверждена Result URL',
    )

    class Meta:
        verbose_name = 'Попытка оплаты Robokassa'
        verbose_name_plural = 'Попытки оплаты Robokassa'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='payments_attempt_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(currency='KZT'),
                name='payments_attempt_currency_kzt',
            ),
            models.CheckConstraint(
                condition=models.Q(mode='test'),
                name='payments_attempt_mode_test',
            ),
            models.CheckConstraint(
                condition=models.Q(hash_algo='sha256'),
                name='payments_attempt_algo_sha256',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(inv_id__gte=INVID_MIN)
                    & models.Q(inv_id__lte=INVID_MAX)
                ),
                name='payments_attempt_invid_range',
            ),
        ]
        indexes = [
            models.Index(
                fields=['order', 'status', 'created_at'],
                name='pay_attempt_order_st_idx',
            ),
        ]

    def __str__(self):
        return f'Robokassa test InvId={self.inv_id} order={self.order_id}'

    @property
    def is_confirmed(self):
        return self.status == self.STATUS_CONFIRMED
