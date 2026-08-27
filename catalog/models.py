from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class Country(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name='Страна')

    class Meta:
        verbose_name = 'Страна'
        verbose_name_plural = 'Страны'
        ordering = ['name']

    def __str__(self):
        return self.name


class Brand(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name='brands',
        verbose_name='Страна'
    )
    name = models.CharField(max_length=100, verbose_name='Марка')

    class Meta:
        verbose_name = 'Марка'
        verbose_name_plural = 'Марки'
        ordering = ['name']
        unique_together = ('country', 'name')

    def __str__(self):
        return self.name


class CarModel(models.Model):
    brand = models.ForeignKey(
        Brand,
        on_delete=models.CASCADE,
        related_name='models',
        verbose_name='Марка'
    )
    name = models.CharField(max_length=100, verbose_name='Модель')

    class Meta:
        verbose_name = 'Модель'
        verbose_name_plural = 'Модели'
        ordering = ['name']
        unique_together = ('brand', 'name')

    def __str__(self):
        return f'{self.brand.name} {self.name}'


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name='Категория')

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['name']

    def __str__(self):
        return self.name


DEFAULT_SELLER_WORK_HOURS = 'Пн–Сб: 09:00 – 18:00, Вс: выходной'
DEFAULT_SELLER_DELIVERY_INFO = (
    'Самовывоз, Доставка курьером по городу, '
    'Доставка по регионам Казахстана (ТК, Казпочта, попутный транспорт)'
)


class SellerProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='seller_profile',
        verbose_name='Пользователь'
    )
    name = models.CharField(max_length=255, verbose_name='Название маркета')
    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True,
        null=True,
        verbose_name='URL-адрес магазина',
    )
    phone = models.CharField(max_length=30, verbose_name='Телефон / WhatsApp')
    city = models.CharField(max_length=120, blank=True, default='', verbose_name='Город')

    address = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Адрес магазина / офиса',
    )

    pickup_address = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Адрес самовывоза',
    )

    pickup_available = models.BooleanField(
        default=True,
        verbose_name='Самовывоз доступен',
    )

    pickup_same_as_store = models.BooleanField(
        default=True,
        verbose_name='Адрес самовывоза совпадает с адресом магазина',
    )

    work_hours = models.CharField(
        max_length=255,
        blank=True,
        default=DEFAULT_SELLER_WORK_HOURS,
        verbose_name='График работы',
    )

    delivery_info = models.TextField(
        blank=True,
        default=DEFAULT_SELLER_DELIVERY_INFO,
        verbose_name='Доставка и оплата',
    )

    instagram = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Instagram'
    )

    website = models.URLField(
        blank=True,
        default='',
        verbose_name='Сайт'
    )

    description = models.TextField(
        blank=True,
        default='',
        verbose_name='Описание маркета'
    )

    logo = models.ImageField(
        upload_to='seller_logos/',
        null=True,
        blank=True,
        verbose_name='Логотип маркета'
    )

    wholesale_enabled = models.BooleanField(
        default=False,
        verbose_name='Оптовая витрина',
        help_text='Включает публичную постоянную оптовую витрину продавца.',
    )

    wholesale_min_order_qty = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(1)],
        verbose_name='Минимум опта, шт.',
        help_text='Минимальная сумма количества всех оптовых товаров в одном заказе.',
    )

    class Meta:
        verbose_name = 'Профиль продавца'
        verbose_name_plural = 'Профили продавцов'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_effective_pickup_address(self):
        """Address shown/used for pickup when self-pickup is available."""
        if not self.pickup_available:
            return ''
        if self.pickup_same_as_store:
            return (self.address or '').strip()
        return (self.pickup_address or '').strip()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')

        if self.pickup_same_as_store:
            synced_pickup = (self.address or '').strip()
            if self.pickup_address != synced_pickup:
                self.pickup_address = synced_pickup
                if update_fields is not None:
                    kwargs['update_fields'] = list(set(update_fields) | {'pickup_address'})

        if not self.slug:
            base_slug = slugify(self.name, allow_unicode=True) if self.name else ''
            if not base_slug:
                base_slug = 'seller'

            slug = base_slug
            counter = 1

            while SellerProfile.objects.filter(
                slug=slug
            ).exclude(
                pk=self.pk
            ).exists():
                counter += 1
                slug = f'{base_slug}-{counter}'

            self.slug = slug
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(set(kwargs['update_fields']) | {'slug'})

        super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet):
    def owned_by_seller(self, seller):
        """Canonical seller_profile, plus unbound legacy seller_name match.

        Products already tied to another SellerProfile are never included,
        even if seller_name happens to match.
        """
        if seller is None:
            return self.none()
        return self.filter(
            models.Q(seller_profile=seller)
            | models.Q(
                seller_profile__isnull=True,
                seller_name__iexact=seller.name,
            )
        )


class Product(models.Model):
    CONDITION_CHOICES = [
        ('new', 'Новая'),
        ('used', 'Б/у'),
    ]

    STATUS_CHOICES = [
        ('active', 'Активен'),
        ('hidden', 'Скрыт'),
        ('sold', 'Продан'),
    ]

    SUPPLIER_LOCAL = 'local'
    SUPPLIER_PHAETON = 'phaeton'

    SUPPLIER_CHOICES = [
        (SUPPLIER_LOCAL, 'Локальный склад'),
        (SUPPLIER_PHAETON, 'Phaeton (внешний API)'),
    ]

    title = models.CharField(
        max_length=255,
        verbose_name='Название товара'
    )

    slug = models.SlugField(
        max_length=255,
        unique=False,
        blank=True,
        default='',
        verbose_name='SEO ссылка'
    )

    article = models.CharField(
        max_length=100,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Артикул'
    )

    price = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Цена',
    )

    price_on_request = models.BooleanField(
        default=False,
        verbose_name='Цена по запросу',
    )

    condition = models.CharField(
        max_length=10,
        choices=CONDITION_CHOICES,
        default='new',
        verbose_name='Состояние'
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='Статус'
    )

    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        verbose_name='Марка'
    )

    car_model = models.ForeignKey(
        CarModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        verbose_name='Модель'
    )

    selected_brands = models.ManyToManyField(
        Brand,
        blank=True,
        related_name='multi_products',
        verbose_name='Марки товара'
    )

    selected_models = models.ManyToManyField(
        CarModel,
        blank=True,
        related_name='multi_products',
        verbose_name='Модели товара'
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        verbose_name='Категория'
    )

    seller_name = models.CharField(
        max_length=255,
        verbose_name='Продавец'
    )

    whatsapp_number = models.CharField(
        max_length=30,
        verbose_name='WhatsApp'
    )

    city = models.CharField(
        max_length=120,
        blank=True,
        default='',
        verbose_name='Город'
    )

    seller_profile = models.ForeignKey(
        SellerProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_products',
        verbose_name='Профиль продавца',
        help_text='Явная привязка к кабинету продавца. Старые товары можно не заполнять.',
    )

    cost_price = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Себестоимость',
        help_text='Только для внутреннего учёта. Не показывается на сайте.',
    )

    stock_qty = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Остаток',
        help_text='Пустое значение не означает «нет в наличии» у старых товаров.',
    )

    main_image = models.ImageField(
        upload_to='products/',
        null=True,
        blank=True,
        verbose_name='Главное фото'
    )

    compatibility = models.TextField(
        blank=True,
        verbose_name='Совместимость'
    )

    engine_compatibility = models.TextField(
        blank=True,
        default='',
        verbose_name='Двигатели',
        help_text='Применяемость по двигателям. По одному значению на строку или через точку с запятой. Без HTML.',
    )

    oem_cross_references = models.TextField(
        blank=True,
        default='',
        verbose_name='OEM / кросс-номера',
        help_text='OEM и кросс-номера. По одному значению на строку или через точку с запятой. Без HTML и ссылок.',
    )

    publish_to_sellers = models.BooleanField(
        default=False,
        verbose_name='Публиковать продавцам',
        help_text=(
            'Разрешает товар в коммерческих рассылках продавцам. '
            'Не зависит от status. Importer не меняет этот флаг.'
        ),
    )

    publish_to_kaspi = models.BooleanField(
        default=False,
        verbose_name='Разрешить публикацию в Kaspi',
        help_text=(
            'Мастер-разрешение для Kaspi. Реальная публикация карточки: '
            'Product.publish_to_kaspi AND listing.publish_to_kaspi AND listing.is_active. '
            'Importer не меняет этот флаг.'
        ),
    )

    description = models.TextField(
        blank=True,
        verbose_name='Описание'
    )

    supplier = models.CharField(
        max_length=32,
        choices=SUPPLIER_CHOICES,
        default=SUPPLIER_LOCAL,
        db_index=True,
        verbose_name='Поставщик',
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано'
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено'
    )

    objects = ProductQuerySet.as_manager()

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['article', 'brand', 'supplier'],
                name='cat_prod_lookup_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['seller_profile', 'article'],
                condition=(
                    models.Q(seller_profile__isnull=False)
                    & ~models.Q(article='')
                ),
                name='uniq_prod_article_per_seller',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(stock_qty__isnull=True)
                    | models.Q(stock_qty__gte=0)
                ),
                name='catalog_product_stock_qty_gte_0',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cost_price__isnull=True)
                    | models.Q(cost_price__gte=0)
                ),
                name='catalog_product_cost_price_gte_0',
            ),
        ]

    def __str__(self):
        if self.article:
            return f'{self.title} ({self.article})'
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        if self.slug:
            return reverse('product_detail', kwargs={'slug': self.slug})
        return reverse('product_detail_old', kwargs={'pk': self.pk})

    def get_whatsapp_inquiry_message(self):
        brand = self.brand.name if self.brand else 'не указан'
        article = self.article or 'не указан'
        product_url = f'https://zpt.kz{self.get_absolute_url()}'
        if self.price_on_request:
            return (
                'Здравствуйте! Я пишу с сайта ZPT.kz. '
                f'Меня интересует товар «{self.title}» '
                f'(Арт. {article}, Бренд: {brand}). '
                'Подскажите, пожалуйста, актуальную цену и наличие. '
                f'Ссылка на товар: {product_url}'
            )
        return (
            'Здравствуйте! Я пишу с сайта ZPT.kz. '
            f'Меня интересует деталь: {self.title} '
            f'(Арт. {article}, Бренд: {brand}). '
            'Подскажите, пожалуйста, по наличию и доставке. '
            f'Ссылка на товар: {product_url}'
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.stock_qty is not None and self.stock_qty < 0:
            errors['stock_qty'] = 'Остаток не может быть отрицательным.'
        if self.cost_price is not None and self.cost_price < 0:
            errors['cost_price'] = 'Себестоимость не может быть отрицательной.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            parts = [self.title]

            if self.brand:
                parts.append(self.brand.name)

            if self.car_model:
                parts.append(self.car_model.name)

            base_slug = slugify(
                '-'.join(parts),
                allow_unicode=False
            )

            if not base_slug:
                base_slug = 'product'

            slug = base_slug
            counter = 1

            while Product.objects.filter(
                slug=slug
            ).exclude(
                pk=self.pk
            ).exists():

                counter += 1
                slug = f'{base_slug}-{counter}'

            self.slug = slug

        super().save(*args, **kwargs)


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='Товар'
    )

    image = models.ImageField(
        upload_to='products/',
        verbose_name='Фото'
    )

    sort_order = models.PositiveIntegerField(
        default=0,
        verbose_name='Порядок',
    )

    is_primary = models.BooleanField(
        default=False,
        verbose_name='Основное в галерее',
        help_text='Не заменяет главное фото товара (main_image).',
    )

    class Meta:
        verbose_name = 'Фото товара'
        verbose_name_plural = 'Фото товаров'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'Фото для {self.product.title}'


class ProductPriceTier(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='price_tiers',
        verbose_name='Товар',
    )
    min_qty = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='От количества, шт.',
    )
    price = models.PositiveIntegerField(
        verbose_name='Цена за единицу, ₸',
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Активна',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено',
    )

    class Meta:
        verbose_name = 'Оптовая цена'
        verbose_name_plural = 'Оптовые цены'
        ordering = ['min_qty', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'min_qty'],
                name='unique_product_price_tier_min_qty',
            ),
            models.CheckConstraint(
                condition=models.Q(min_qty__gt=0),
                name='product_price_tier_min_qty_gt_0',
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name='product_price_tier_price_gte_0',
            ),
        ]

    def __str__(self):
        return f'{self.product_id}: от {self.min_qty} шт. — {self.price} ₸'

    def clean(self):
        super().clean()
        errors = {}
        if self.min_qty is not None and self.min_qty <= 0:
            errors['min_qty'] = 'Минимальное количество должно быть больше 0.'
        if self.price is not None and self.price < 0:
            errors['price'] = 'Цена не может быть отрицательной.'
        if errors:
            raise ValidationError(errors)


class ProductPromotion(models.Model):
    TYPE_SALE = 'sale'
    TYPE_PROMO = 'promo'

    PROMOTION_TYPE_CHOICES = [
        (TYPE_SALE, 'Распродажа'),
        (TYPE_PROMO, 'Акция'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='promotions',
        verbose_name='Товар',
    )
    promotion_type = models.CharField(
        max_length=16,
        choices=PROMOTION_TYPE_CHOICES,
        verbose_name='Тип',
    )
    price = models.PositiveIntegerField(
        verbose_name='Специальная цена, ₸',
    )
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Начало',
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Окончание',
    )
    qty_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Лимит количества',
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Активна',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено',
    )

    class Meta:
        verbose_name = 'Акция / распродажа'
        verbose_name_plural = 'Акции и распродажи'
        ordering = ['-starts_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name='product_promotion_price_gte_0',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(qty_limit__isnull=True)
                    | models.Q(qty_limit__gte=0)
                ),
                name='product_promotion_qty_limit_gte_0',
            ),
        ]

    def __str__(self):
        return f'{self.get_promotion_type_display()} {self.product_id}: {self.price} ₸'

    def clean(self):
        super().clean()
        errors = {}
        if self.price is not None and self.price < 0:
            errors['price'] = 'Цена не может быть отрицательной.'
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at < self.starts_at
        ):
            errors['ends_at'] = 'Дата окончания не может быть раньше даты начала.'
        if errors:
            raise ValidationError(errors)


class ProductConsignment(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name='consignment',
        verbose_name='Товар',
    )
    enabled = models.BooleanField(
        default=False,
        verbose_name='Доступно на реализацию',
    )
    max_qty = models.PositiveIntegerField(
        default=0,
        verbose_name='Максимальное количество',
    )
    settlement_price = models.PositiveIntegerField(
        default=0,
        verbose_name='Расчётная цена, ₸',
    )
    term_days = models.PositiveIntegerField(
        default=0,
        verbose_name='Срок реализации, дней',
    )
    conditions = models.TextField(
        blank=True,
        default='',
        verbose_name='Дополнительные условия',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено',
    )

    class Meta:
        verbose_name = 'Реализация'
        verbose_name_plural = 'Реализация'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_qty__gte=0),
                name='product_consignment_max_qty_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(settlement_price__gte=0),
                name='product_consignment_price_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(term_days__gte=0),
                name='product_consignment_term_days_gte_0',
            ),
        ]

    def __str__(self):
        state = 'доступно' if self.enabled else 'недоступно'
        return f'Реализация {self.product_id}: {state}'

    def clean(self):
        super().clean()
        errors = {}
        if self.max_qty is not None and self.max_qty < 0:
            errors['max_qty'] = 'Количество не может быть отрицательным.'
        if self.settlement_price is not None and self.settlement_price < 0:
            errors['settlement_price'] = 'Цена не может быть отрицательной.'
        if self.term_days is not None and self.term_days < 0:
            errors['term_days'] = 'Срок не может быть отрицательным.'
        if errors:
            raise ValidationError(errors)


class ProductConsignmentRequest(models.Model):
    STATUS_NEW = 'new'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_ISSUED = 'issued'
    STATUS_CLOSED = 'closed'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_NEW, 'Новая'),
        (STATUS_APPROVED, 'Одобрена'),
        (STATUS_REJECTED, 'Отклонена'),
        (STATUS_ISSUED, 'Выдана'),
        (STATUS_CLOSED, 'Закрыта'),
        (STATUS_CANCELLED, 'Отменена'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='consignment_requests',
        verbose_name='Товар',
    )
    seller_profile = models.ForeignKey(
        SellerProfile,
        on_delete=models.PROTECT,
        related_name='consignment_requests',
        verbose_name='Продавец',
    )
    requested_qty = models.PositiveIntegerField(
        verbose_name='Запрошенное количество',
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
        verbose_name='Статус',
    )
    settlement_price = models.PositiveIntegerField(
        verbose_name='Расчётная цена на момент заявки, ₸',
        help_text='Снимок условий. Позднее изменение реализации товар не меняет эту заявку.',
    )
    term_days = models.PositiveIntegerField(
        default=0,
        verbose_name='Срок реализации, дней',
    )
    conditions = models.TextField(
        blank=True,
        default='',
        verbose_name='Условия на момент заявки',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено',
    )

    class Meta:
        verbose_name = 'Заявка на реализацию'
        verbose_name_plural = 'Заявки на реализацию'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'Заявка #{self.pk or "new"}: '
            f'{self.product_id} × {self.requested_qty}'
        )


class ProductFulfillment(models.Model):
    SOURCE_WMS = 'wms'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_WMS, 'WMS'),
        (SOURCE_MANUAL, 'Вручную'),
    ]

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name='fulfillment',
        verbose_name='Товар',
    )
    external_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name='Fulfillment / WMS ID',
        help_text=(
            'Внешний идентификатор склада. Не заполнять автоматически из артикула: '
            'в текущем WMS Excel отдельного надёжного Fulfillment ID нет.'
        ),
    )
    source = models.CharField(
        max_length=32,
        choices=SOURCE_CHOICES,
        default=SOURCE_WMS,
        verbose_name='Источник',
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последняя синхронизация',
    )

    class Meta:
        verbose_name = 'Fulfillment-идентификатор'
        verbose_name_plural = 'Fulfillment-идентификаторы'

    def __str__(self):
        return f'{self.product_id}: {self.external_id or "—"}'


class ProductBarcode(models.Model):
    SOURCE_WMS = 'wms'
    SOURCE_KASPI = 'kaspi'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_WMS, 'WMS'),
        (SOURCE_KASPI, 'Kaspi'),
        (SOURCE_MANUAL, 'Вручную'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='barcodes',
        verbose_name='Товар',
    )
    code = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name='Штрихкод',
    )
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default=SOURCE_WMS,
        verbose_name='Источник',
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name='Основной',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Штрихкод товара'
        verbose_name_plural = 'Штрихкоды товаров'
        ordering = ['-is_primary', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'code'],
                name='uniq_product_barcode_code',
            ),
        ]
        indexes = [
            models.Index(fields=['code'], name='cat_barcode_code_idx'),
        ]

    def __str__(self):
        return f'{self.product_id}: {self.code}'


class ProductKaspiListing(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='kaspi_listings',
        verbose_name='Товар',
    )
    master_sku = models.CharField(
        max_length=128,
        db_index=True,
        verbose_name='Kaspi master SKU',
        help_text='Идентификатор карточки Kaspi (колонка SKU в выгрузке).',
    )
    merchant_sku = models.CharField(
        max_length=128,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Kaspi merchant SKU',
        help_text='Артикул продавца в Kaspi. Не путать с master_sku.',
    )
    barcode = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Штрихкод Kaspi',
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name='Листинг активен',
    )
    publish_to_kaspi = models.BooleanField(
        default=False,
        verbose_name='Публиковать этот листинг в Kaspi',
    )
    last_known_our_price = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Последняя наша цена в Kaspi',
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последняя синхронизация',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Kaspi-листинг'
        verbose_name_plural = 'Kaspi-листинги'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'master_sku'],
                name='uniq_product_kaspi_master_sku',
            ),
        ]
        indexes = [
            models.Index(
                fields=['product', 'is_active'],
                name='cat_kaspi_listing_active_idx',
            ),
        ]

    def __str__(self):
        return f'{self.product_id}: {self.master_sku}'

    def is_effectively_published_to_kaspi(self):
        return bool(
            self.product.publish_to_kaspi
            and self.publish_to_kaspi
            and self.is_active
        )


class CatalogImportBatch(models.Model):
    SOURCE_AG_PARTS = 'ag_parts'
    SOURCE_AG_PARTS_BARCODES = 'ag_parts_barcodes'

    MODE_WRITE = 'write'
    MODE_DRY_RUN = 'dry-run'
    MODE_CHOICES = [
        (MODE_WRITE, 'Write'),
        (MODE_DRY_RUN, 'Dry-run'),
    ]

    SCOPE_FULL = 'full'
    SCOPE_PARTIAL = 'partial'
    SCOPE_CHOICES = [
        (SCOPE_FULL, 'Полный каталог'),
        (SCOPE_PARTIAL, 'Частичный импорт'),
    ]

    STATUS_SUCCESS = 'success'
    STATUS_BLOCKED = 'blocked'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Успех'),
        (STATUS_BLOCKED, 'Блок (shrink guard)'),
        (STATUS_ERROR, 'Ошибка'),
    ]

    ARCHIVE_NOT_APPLICABLE = 'not_applicable'
    ARCHIVE_SUCCESS = 'success'
    ARCHIVE_ERROR = 'error'
    ARCHIVE_STATUS_CHOICES = [
        (ARCHIVE_NOT_APPLICABLE, 'Не применимо'),
        (ARCHIVE_SUCCESS, 'Архив сохранён'),
        (ARCHIVE_ERROR, 'Ошибка архива'),
    ]

    seller_profile = models.ForeignKey(
        SellerProfile,
        on_delete=models.PROTECT,
        related_name='catalog_import_batches',
        verbose_name='Профиль продавца',
    )
    source = models.CharField(
        max_length=64,
        default=SOURCE_AG_PARTS,
        db_index=True,
        verbose_name='Источник',
    )
    filename = models.CharField(max_length=255, verbose_name='Имя файла')
    file_sha256 = models.CharField(max_length=64, db_index=True, verbose_name='SHA256 источника')
    source_archive_path = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Путь архива источника',
    )
    archive_status = models.CharField(
        max_length=16,
        choices=ARCHIVE_STATUS_CHOICES,
        default=ARCHIVE_NOT_APPLICABLE,
        db_index=True,
        verbose_name='Статус архива',
    )
    archive_error = models.TextField(
        blank=True,
        default='',
        verbose_name='Ошибка архива',
    )
    started_at = models.DateTimeField(verbose_name='Начало')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='Окончание')
    mode = models.CharField(
        max_length=16,
        choices=MODE_CHOICES,
        default=MODE_WRITE,
        verbose_name='Режим',
    )
    source_scope = models.CharField(
        max_length=16,
        choices=SCOPE_CHOICES,
        default=SCOPE_PARTIAL,
        db_index=True,
        verbose_name='Охват источника',
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_SUCCESS,
        db_index=True,
        verbose_name='Статус',
    )
    source_row_count = models.PositiveIntegerField(default=0, verbose_name='Строк в источнике')
    source_unique_count = models.PositiveIntegerField(default=0, verbose_name='Уникальных в источнике')
    selected_count = models.PositiveIntegerField(default=0, verbose_name='Выбрано после фильтра')
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    conflict_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    missing_from_source_count = models.PositiveIntegerField(default=0)
    previous_successful_batch = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='following_batches',
        verbose_name='Предыдущий успешный full write',
    )
    blocked_reason = models.TextField(blank=True, default='', verbose_name='Причина блока')
    allow_source_shrink_reason = models.TextField(
        blank=True,
        default='',
        verbose_name='Причина обхода shrink guard',
    )

    class Meta:
        verbose_name = 'Пакет импорта каталога'
        verbose_name_plural = 'Пакеты импорта каталога'
        ordering = ['-started_at', '-id']
        indexes = [
            models.Index(
                fields=['seller_profile', 'source', 'source_scope', 'mode', 'status'],
                name='cat_import_batch_lookup_idx',
            ),
        ]

    def __str__(self):
        return f'ImportBatch #{self.pk} {self.source} {self.status}'


class CatalogImportItem(models.Model):
    ACTION_CREATED = 'created'
    ACTION_UPDATED = 'updated'
    ACTION_UNCHANGED = 'unchanged'
    ACTION_SKIPPED = 'skipped'
    ACTION_CONFLICT = 'conflict'
    ACTION_ERROR = 'error'
    ACTION_MISSING_FROM_SOURCE = 'missing_from_source'
    ACTION_CHOICES = [
        (ACTION_CREATED, 'Created'),
        (ACTION_UPDATED, 'Updated'),
        (ACTION_UNCHANGED, 'Unchanged'),
        (ACTION_SKIPPED, 'Skipped'),
        (ACTION_CONFLICT, 'Conflict'),
        (ACTION_ERROR, 'Error'),
        (ACTION_MISSING_FROM_SOURCE, 'Missing from source'),
    ]

    batch = models.ForeignKey(
        CatalogImportBatch,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Пакет',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalog_import_items',
        verbose_name='Товар',
    )
    article = models.CharField(max_length=100, db_index=True, verbose_name='Артикул')
    action = models.CharField(
        max_length=32,
        choices=ACTION_CHOICES,
        verbose_name='Действие',
    )
    warnings = models.JSONField(default=list, blank=True, verbose_name='Предупреждения')
    errors = models.JSONField(default=list, blank=True, verbose_name='Ошибки')
    changed_fields = models.JSONField(default=dict, blank=True, verbose_name='Изменённые поля')

    class Meta:
        verbose_name = 'Строка импорта каталога'
        verbose_name_plural = 'Строки импорта каталога'
        ordering = ['id']
        indexes = [
            models.Index(fields=['batch', 'action'], name='cat_import_item_action_idx'),
            models.Index(fields=['article'], name='cat_import_item_article_idx'),
        ]

    def __str__(self):
        return f'{self.article} ({self.action})'