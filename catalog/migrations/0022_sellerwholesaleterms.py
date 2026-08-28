from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0021_sellerprofile_wholesale'),
    ]

    operations = [
        migrations.CreateModel(
            name='SellerWholesaleTerms',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'vat_mode',
                    models.CharField(
                        choices=[
                            ('unspecified', 'Не указано'),
                            ('included', 'С НДС'),
                            ('excluded', 'Без НДС'),
                        ],
                        default='unspecified',
                        max_length=20,
                        verbose_name='НДС',
                    ),
                ),
                (
                    'prepayment_percent',
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                        validators=[
                            MinValueValidator(0),
                            MaxValueValidator(100),
                        ],
                        verbose_name='Предоплата, %',
                    ),
                ),
                (
                    'confirm_stock_before_payment',
                    models.BooleanField(
                        default=True,
                        verbose_name='Подтверждать наличие до оплаты',
                    ),
                ),
                (
                    'provides_invoice',
                    models.BooleanField(default=False, verbose_name='Счет на оплату'),
                ),
                (
                    'provides_waybill',
                    models.BooleanField(default=False, verbose_name='Накладная'),
                ),
                (
                    'provides_esf',
                    models.BooleanField(default=False, verbose_name='ЭСФ'),
                ),
                (
                    'pickup_enabled',
                    models.BooleanField(default=False, verbose_name='Самовывоз'),
                ),
                (
                    'pickup_city',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=120,
                        verbose_name='Город самовывоза',
                    ),
                ),
                (
                    'delivery_kz_enabled',
                    models.BooleanField(
                        default=False,
                        verbose_name='Доставка по Казахстану',
                    ),
                ),
                (
                    'delivery_payer',
                    models.CharField(
                        choices=[
                            ('buyer', 'Покупатель'),
                            ('seller', 'Продавец'),
                            ('agreement', 'По согласованию'),
                        ],
                        default='agreement',
                        max_length=20,
                        verbose_name='Кто оплачивает доставку',
                    ),
                ),
                (
                    'primary_carrier',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=120,
                        verbose_name='Основная ТК',
                    ),
                ),
                (
                    'primary_carrier_service',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=120,
                        verbose_name='Тариф ТК',
                    ),
                ),
                (
                    'primary_carrier_url',
                    models.URLField(
                        blank=True,
                        default='',
                        verbose_name='Сайт ТК',
                    ),
                ),
                (
                    'other_carrier_allowed',
                    models.BooleanField(
                        default=True,
                        verbose_name='Другая ТК по согласованию',
                    ),
                ),
                (
                    'stock_note',
                    models.CharField(
                        blank=True,
                        default='',
                        max_length=255,
                        verbose_name='Примечание по наличию',
                    ),
                ),
                (
                    'seller',
                    models.OneToOneField(
                        on_delete=models.CASCADE,
                        related_name='wholesale_terms',
                        to='catalog.sellerprofile',
                        verbose_name='Продавец',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Оптовые условия продавца',
                'verbose_name_plural': 'Оптовые условия продавцов',
            },
        ),
    ]
