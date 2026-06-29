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
        verbose_name='Адрес склада',
    )

    work_hours = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='График работы',
    )

    delivery_info = models.TextField(
        blank=True,
        default='',
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

    class Meta:
        verbose_name = 'Профиль продавца'
        verbose_name_plural = 'Профили продавцов'
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
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

        super().save(*args, **kwargs)


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
        verbose_name='Артикул'
    )

    price = models.PositiveIntegerField(
        verbose_name='Цена'
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

    description = models.TextField(
        blank=True,
        verbose_name='Описание'
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Создано'
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Обновлено'
    )

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['-created_at']

    def __str__(self):
        if self.article:
            return f'{self.title} ({self.article})'
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        if self.slug:
            return reverse('product_detail', kwargs={'slug': self.slug})
        return reverse('product_detail_old', kwargs={'pk': self.pk})

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

    class Meta:
        verbose_name = 'Фото товара'
        verbose_name_plural = 'Фото товаров'

    def __str__(self):
        return f'Фото для {self.product.title}'