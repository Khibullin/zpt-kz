from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Order(models.Model):
    STATUS_NEW = 'new'
    STATUS_PENDING_PAYMENT = 'pending_payment'
    STATUS_PAID = 'paid'
    STATUS_CANCELED = 'canceled'

    STATUS_CHOICES = [
        (STATUS_NEW, 'Новый / Создан'),
        (STATUS_PENDING_PAYMENT, 'Ожидает оплаты Kaspi'),
        (STATUS_PAID, 'Оплачен / Передан на сборку поставщику'),
        (STATUS_CANCELED, 'Отменен'),
    ]

    DELIVERY_PICKUP = 'pickup'
    DELIVERY_COURIER = 'courier'
    DELIVERY_KZ = 'kz_delivery'

    DELIVERY_METHOD_CHOICES = [
        (DELIVERY_PICKUP, 'Самовывоз'),
        (DELIVERY_COURIER, 'Курьер по городу'),
        (DELIVERY_KZ, 'Доставка по Казахстану'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        verbose_name='Пользователь',
    )
    customer_name = models.CharField(max_length=255, verbose_name='Имя покупателя')
    customer_phone = models.CharField(max_length=30, verbose_name='Телефон')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
        verbose_name='Статус',
    )
    total_price = models.PositiveIntegerField(verbose_name='Сумма заказа, ₸')
    delivery_method = models.CharField(
        max_length=20,
        choices=DELIVERY_METHOD_CHOICES,
        verbose_name='Способ доставки',
    )
    delivery_address = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Данные доставки',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлен')

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']

    def __str__(self):
        return f'Заказ #{self.pk} — {self.customer_name}'

    @property
    def delivery_method_label(self):
        return dict(self.DELIVERY_METHOD_CHOICES).get(self.delivery_method, self.delivery_method)


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Заказ',
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        related_name='order_items',
        verbose_name='Товар',
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name='Количество')
    price_at_purchase = models.PositiveIntegerField(verbose_name='Цена на момент покупки, ₸')

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'

    def __str__(self):
        return f'{self.product.title} × {self.quantity}'

    @property
    def line_total(self):
        return self.price_at_purchase * self.quantity


class KaspiTransaction(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='kaspi_transactions',
        verbose_name='Заказ',
    )
    kaspi_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name='ID транзакции Kaspi',
    )
    status = models.CharField(max_length=64, verbose_name='Статус')
    raw_response = models.JSONField(default=dict, blank=True, verbose_name='Ответ банка')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')

    class Meta:
        verbose_name = 'Транзакция Kaspi'
        verbose_name_plural = 'Транзакции Kaspi'
        ordering = ['-created_at']

    def __str__(self):
        return f'Kaspi {self.kaspi_id or "—"} для заказа #{self.order_id}'
