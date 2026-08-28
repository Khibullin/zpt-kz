import uuid

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Order(models.Model):
    STATUS_NEW = 'new'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_AWAITING_PAYMENT = 'awaiting_payment'
    STATUS_PAID = 'paid'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_NEW, 'Новый'),
        (STATUS_CONFIRMED, 'Подтверждён продавцом'),
        (STATUS_AWAITING_PAYMENT, 'Ожидает оплаты'),
        (STATUS_PAID, 'Оплачен'),
        (STATUS_CANCELLED, 'Отменён'),
    ]

    DELIVERY_PICKUP = 'pickup'
    DELIVERY_COURIER = 'courier'
    DELIVERY_KZ = 'kz_delivery'

    DELIVERY_METHOD_CHOICES = [
        (DELIVERY_PICKUP, 'Самовывоз'),
        (DELIVERY_COURIER, 'Курьер по городу'),
        (DELIVERY_KZ, 'Доставка по Казахстану'),
    ]

    ORDER_TYPE_RETAIL = 'retail'
    ORDER_TYPE_WHOLESALE = 'wholesale'
    ORDER_TYPE_CHOICES = [
        (ORDER_TYPE_RETAIL, 'Розничный'),
        (ORDER_TYPE_WHOLESALE, 'Оптовый'),
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
    seller_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Продавец',
    )
    seller_whatsapp = models.CharField(
        max_length=30,
        blank=True,
        default='',
        verbose_name='WhatsApp продавца',
    )
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
    order_type = models.CharField(
        max_length=20,
        choices=ORDER_TYPE_CHOICES,
        default=ORDER_TYPE_RETAIL,
        db_index=True,
        verbose_name='Тип заказа',
    )
    utm_source = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='UTM source',
    )
    utm_medium = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='UTM medium',
    )
    utm_campaign = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='UTM campaign',
    )
    wholesale_terms_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Снимок оптовых условий',
    )
    access_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name='Токен доступа',
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

    @property
    def is_wholesale(self):
        return self.order_type == self.ORDER_TYPE_WHOLESALE

    @property
    def total_quantity(self):
        return sum(item.quantity for item in self.items.all())


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


class CartItem(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='cart_items',
        verbose_name='Пользователь',
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.CASCADE,
        related_name='cart_items',
        verbose_name='Товар',
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name='Количество')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Корзина'
        verbose_name_plural = 'Корзины пользователей'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'product'],
                name='unique_user_cart_product',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}: {self.product_id} × {self.quantity}'


class WholesaleFunnelEvent(models.Model):
    EVENT_STOREFRONT_VIEW = 'storefront_view'
    EVENT_PRODUCT_VIEW = 'product_view'
    EVENT_PRICE_DOWNLOAD = 'price_download'
    EVENT_ADD_TO_CART = 'add_to_cart'
    EVENT_CHECKOUT_VIEW = 'checkout_view'
    EVENT_ORDER_CREATED = 'order_created'

    EVENT_CHOICES = [
        (EVENT_STOREFRONT_VIEW, 'Витрина'),
        (EVENT_PRODUCT_VIEW, 'Карточка товара'),
        (EVENT_PRICE_DOWNLOAD, 'Скачивание прайса'),
        (EVENT_ADD_TO_CART, 'Добавление в корзину'),
        (EVENT_CHECKOUT_VIEW, 'Оформление'),
        (EVENT_ORDER_CREATED, 'Заказ'),
    ]

    event_type = models.CharField(
        max_length=32,
        choices=EVENT_CHOICES,
        db_index=True,
        verbose_name='Тип события',
    )
    seller_profile = models.ForeignKey(
        'catalog.SellerProfile',
        on_delete=models.PROTECT,
        related_name='wholesale_funnel_events',
        verbose_name='Продавец',
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wholesale_funnel_events',
        verbose_name='Товар',
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wholesale_funnel_events',
        verbose_name='Заказ',
    )
    visitor_id = models.UUIDField(db_index=True, verbose_name='Посетитель')
    utm_source = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='UTM source',
    )
    utm_medium = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='UTM medium',
    )
    utm_campaign = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='UTM campaign',
    )
    quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Количество',
    )
    value_kzt = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Сумма, ₸',
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Метаданные',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Создано',
    )

    class Meta:
        verbose_name = 'Событие оптовой воронки'
        verbose_name_plural = 'События оптовой воронки'
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['seller_profile', 'event_type', 'created_at'],
                name='wh_funnel_seller_evt_idx',
            ),
            models.Index(
                fields=['utm_campaign', 'created_at'],
                name='wh_funnel_campaign_idx',
            ),
            models.Index(
                fields=['visitor_id', 'event_type', 'created_at'],
                name='wh_funnel_visitor_evt_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['order'],
                condition=models.Q(event_type='order_created'),
                name='uniq_wholesale_funnel_order_created',
            ),
        ]

    def __str__(self):
        return f'{self.event_type} @ {self.created_at:%Y-%m-%d %H:%M}'
