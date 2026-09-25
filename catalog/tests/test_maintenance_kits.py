from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from io import StringIO

from catalog.commercial import resolve_commercial_price
from catalog.maintenance_kit_seed import (
    COVER_SPARKS_EXCLUDED_CAPTION,
    UNI_K_INCOMPLETE_WARNING,
    apply_maintenance_kits,
    plan_maintenance_kits,
)
from catalog.maintenance_kits import (
    available_kits,
    build_kit_view,
    guest_kit_base_price,
    LINE_UNAVAILABLE,
    LINE_UNCONFIRMED,
    OEM_UNKNOWN_LABEL,
)
from catalog.models import (
    Brand,
    CarModel,
    Country,
    MaintenanceKit,
    MaintenanceKitCarRequest,
    MaintenanceKitItem,
    Product,
    ProductPriceTier,
    SellerProfile,
)
from core.models import Match, Request as PartsRequest
from orders.constants import SESSION_CART_KEY
from orders.models import CartItem


def _make_country():
    return Country.objects.create(name='Китай KIT')


def _make_product(**kwargs):
    defaults = {
        'title': 'Тестовый товар комплекта',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'KIT-TEST',
        'stock_qty': 10,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class MaintenanceKitModelTests(TestCase):
    def setUp(self):
        country = _make_country()
        self.brand = Brand.objects.create(country=country, name='Chery')
        self.other_brand = Brand.objects.create(country=country, name='Exeed')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7 Pro')
        self.other_model = CarModel.objects.create(brand=self.other_brand, name='TXL')

    def test_quantity_must_be_at_least_one(self):
        kit = MaintenanceKit.objects.create(
            name='Комплект',
            slug='kit-qty',
            brand=self.brand,
            car_model=self.model,
            engine='1.5T',
            is_active=True,
        )
        product = _make_product(article='QTY-1')
        item = MaintenanceKitItem(kit=kit, product=product, quantity=0)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_duplicate_product_in_same_kit_rejected(self):
        kit = MaintenanceKit.objects.create(
            name='Комплект',
            slug='kit-dup',
            brand=self.brand,
            car_model=self.model,
            is_active=True,
        )
        product = _make_product(article='DUP-1')
        MaintenanceKitItem.objects.create(kit=kit, product=product, quantity=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MaintenanceKitItem.objects.create(kit=kit, product=product, quantity=2)

    def test_model_must_match_brand(self):
        kit = MaintenanceKit(
            name='Комплект',
            slug='kit-brand-mismatch',
            brand=self.brand,
            car_model=self.other_model,
        )
        with self.assertRaises(ValidationError):
            kit.full_clean()


class MaintenanceKitPricingStockTests(TestCase):
    def setUp(self):
        country = _make_country()
        self.brand = Brand.objects.create(country=country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7 Pro')
        self.air = _make_product(article='AIR-1', title='Воздушный', price=1700, stock_qty=10)
        self.cabin = _make_product(article='CAB-1', title='Салонный', price=1650, stock_qty=10)
        self.oil = _make_product(article='OIL-1', title='Масляный', price=1309, stock_qty=10)
        self.spark = _make_product(article='SPARK-1', title='Свеча', price=2450, stock_qty=10)
        self.kit = MaintenanceKit.objects.create(
            name='Комплект ТО тест',
            slug='kit-price',
            brand=self.brand,
            car_model=self.model,
            engine='1.5T',
            is_active=True,
        )
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.air, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.cabin, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.oil, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.spark, quantity=4)

    def test_kit_price_uses_resolve_commercial_price(self):
        view = build_kit_view(self.kit)
        expected = 0
        for line in view.lines:
            quote = resolve_commercial_price(line.product, line.quantity, seller_profile=None)
            self.assertTrue(quote.can_buy)
            self.assertEqual(line.unit_price, quote.unit_price)
            self.assertEqual(line.subtotal, quote.total_price)
            expected += quote.total_price
        self.assertEqual(view.total_price, expected)
        self.assertEqual(
            expected,
            1700 + 1650 + 1309 + (2450 * 4),
        )
        self.assertEqual(guest_kit_base_price(self.kit), expected)

    def test_logged_in_seller_kit_price_matches_commercial_quote(self):
        user = User.objects.create_user(username='seller-kit', password='secret12345')
        seller = SellerProfile.objects.create(
            user=user,
            name='Test Seller Kit',
            phone='77770001122',
            city='Алматы',
        )
        ProductPriceTier.objects.create(
            product=self.air,
            min_qty=1,
            price=400,
            is_active=True,
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        view = response.context['kit_view']
        air_line = next(line for line in view.lines if line.product.pk == self.air.id)
        quote = resolve_commercial_price(self.air, 1, seller_profile=seller)
        self.assertEqual(quote.unit_price, 400)
        self.assertEqual(air_line.unit_price, 400)

    def test_product_price_change_updates_kit_total(self):
        before = guest_kit_base_price(self.kit)
        self.oil.price = 2000
        self.oil.save(update_fields=['price'])
        after = guest_kit_base_price(self.kit)
        self.assertEqual(after, before + 691)

    def test_spark_quantity_limits_available_kits(self):
        self.spark.stock_qty = 10
        self.spark.save(update_fields=['stock_qty'])
        self.assertEqual(available_kits(self.kit), 2)

        self.spark.stock_qty = 4
        self.spark.save(update_fields=['stock_qty'])
        self.assertEqual(available_kits(self.kit), 1)

        self.spark.stock_qty = 3
        self.spark.save(update_fields=['stock_qty'])
        self.assertEqual(available_kits(self.kit), 0)

    def test_zero_stock_does_not_hide_kit_and_allows_other_items(self):
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        self.assertEqual(available_kits(self.kit), 0)
        view = build_kit_view(self.kit)
        self.assertTrue(view.can_add)
        self.assertTrue(view.has_unavailable)
        self.assertEqual(view.orderable_count, 3)
        oil_line = next(line for line in view.lines if line.product.pk == self.oil.id)
        self.assertFalse(oil_line.can_buy)
        self.assertFalse(oil_line.selected_by_default)
        self.assertEqual(oil_line.availability, 'unavailable')
        self.assertEqual(view.total_price, 1700 + 1650 + (2450 * 4))
        self.assertEqual(view.full_total_price, 1700 + 1650 + 1309 + (2450 * 4))

    def test_mixed_stock_none_and_numeric(self):
        self.air.stock_qty = None
        self.air.save(update_fields=['stock_qty'])
        self.cabin.stock_qty = None
        self.cabin.save(update_fields=['stock_qty'])
        self.oil.stock_qty = None
        self.oil.save(update_fields=['stock_qty'])
        self.spark.stock_qty = 8
        self.spark.save(update_fields=['stock_qty'])
        self.assertEqual(available_kits(self.kit), 2)

    def test_all_stock_none_available_is_none(self):
        for product in (self.air, self.cabin, self.oil, self.spark):
            product.stock_qty = None
            product.save(update_fields=['stock_qty'])
        self.assertIsNone(available_kits(self.kit))
        view = build_kit_view(self.kit)
        self.assertIsNone(view.available_kits)
        self.assertTrue(view.can_add)

    def test_availability_label_uses_counted_stock_not_orderability_alone(self):
        view = build_kit_view(self.kit)
        air = next(line for line in view.lines if line.product.pk == self.air.id)
        spark = next(line for line in view.lines if line.product.pk == self.spark.id)
        self.assertEqual(air.availability_label, 'В наличии')
        self.assertEqual(spark.availability_label, 'В наличии')
        self.assertEqual(spark.quantity, 4)

        self.air.stock_qty = None
        self.air.save(update_fields=['stock_qty'])
        view = build_kit_view(self.kit)
        air = next(line for line in view.lines if line.product.pk == self.air.id)
        self.assertTrue(air.can_buy)
        self.assertEqual(air.availability_label, 'Можно заказать')

        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        view = build_kit_view(self.kit)
        oil = next(line for line in view.lines if line.product.pk == self.oil.id)
        self.assertFalse(oil.can_buy)
        self.assertEqual(oil.availability_label, 'Нет в наличии')
        self.assertEqual(oil.request_url, '/request-parts/')

        html = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertContains(html, 'Можно заказать')
        self.assertContains(html, 'Нет в наличии')
        self.assertContains(html, 'Оставить заявку')


class MaintenanceKitCartTests(TestCase):
    def setUp(self):
        country = _make_country()
        self.brand = Brand.objects.create(country=country, name='Chery')
        self.model = CarModel.objects.create(brand=self.brand, name='Tiggo 7 Pro')
        self.air = _make_product(article='AIR-C', title='Воздушный', price=1000, stock_qty=10)
        self.cabin = _make_product(article='CAB-C', title='Салонный', price=1000, stock_qty=10)
        self.oil = _make_product(article='OIL-C', title='Масляный', price=1000, stock_qty=10)
        self.spark = _make_product(article='SPK-C', title='Свеча', price=1000, stock_qty=10)
        self.kit = MaintenanceKit.objects.create(
            name='Комплект корзина',
            slug='kit-cart',
            brand=self.brand,
            car_model=self.model,
            engine='1.5T',
            is_active=True,
        )
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.air, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.cabin, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.oil, quantity=1)
        MaintenanceKitItem.objects.create(kit=self.kit, product=self.spark, quantity=4)
        self.client = Client()

    def _add_via_view(self, products=None, extra=None):
        if products is None:
            products = (self.air, self.cabin, self.oil, self.spark)
        data = {'item': [str(product.id) for product in products]}
        if extra:
            data.update(extra)
        return self.client.post(
            reverse('maintenance_kit_add_to_cart', kwargs={'slug': self.kit.slug}),
            data=data,
        )

    def test_add_kit_creates_correct_cart_quantities(self):
        response = self._add_via_view()
        self.assertEqual(response.status_code, 302)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(self.air.id)], 1)
        self.assertEqual(cart[str(self.cabin.id)], 1)
        self.assertEqual(cart[str(self.oil.id)], 1)
        self.assertEqual(cart[str(self.spark.id)], 4)
        self.assertEqual(sum(cart.values()), 7)

    def test_spark_stock_limits_cart_add(self):
        self.spark.stock_qty = 4
        self.spark.save(update_fields=['stock_qty'])
        first = self._add_via_view()
        self.assertEqual(first.status_code, 302)
        second = self._add_via_view()
        self.assertEqual(second.status_code, 302)
        self.assertRedirects(
            second,
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug}),
            fetch_redirect_response=False,
        )
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(self.spark.id)], 4)

    def test_preflight_failure_adds_nothing(self):
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        response = self._add_via_view()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_seller_conflict_inside_kit_adds_nothing(self):
        self.spark.whatsapp_number = '+77011112233'
        self.spark.save(update_fields=['whatsapp_number'])
        response = self._add_via_view()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_seller_conflict_with_existing_cart_adds_nothing(self):
        other = _make_product(
            article='OTHER-C',
            title='Чужой товар',
            whatsapp_number='+77019990000',
            price=500,
            stock_qty=5,
        )
        self.client.post(
            reverse('orders:cart_add_api'),
            data={'product_id': other.id, 'quantity': 1},
        )
        before = dict(self.client.session[SESSION_CART_KEY])
        response = self._add_via_view()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(dict(self.client.session[SESSION_CART_KEY]), before)
        self.assertEqual(before, {str(other.id): 1})

    def test_inactive_kit_not_on_list_or_detail(self):
        self.kit.is_active = False
        self.kit.save(update_fields=['is_active'])
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertNotContains(listing, self.kit.name)
        detail = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertEqual(detail.status_code, 404)

    def test_inactive_kit_post_is_404(self):
        self.kit.is_active = False
        self.kit.save(update_fields=['is_active'])
        response = self._add_via_view()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_detail_page_shows_composition_and_stock_copy(self):
        for product in (self.air, self.cabin, self.oil, self.spark):
            product.stock_qty = None
            product.save(update_fields=['stock_qty'])
        response = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Наличие уточняется')
        self.assertContains(response, self.spark.article)
        self.assertContains(response, 'Добавить выбранное в корзину')
        self.assertContains(response, 'Свеча зажигания — SPK-C')
        self.assertContains(response, 'type="checkbox"')
        self.assertRegex(response.content.decode('utf-8'), r'data-subtotal="\d+"')
        self.assertRegex(
            response.content.decode('utf-8'),
            r'name="item"\s+value="\d+"',
        )
        self.assertNotContains(response, 'class="kit-cover-wrap"')

    def test_detail_hides_foreign_model_from_product_title(self):
        self.spark.title = 'Свеча зажигания другой модели XYZ'
        self.spark.save(update_fields=['title'])
        response = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertContains(response, 'Свеча зажигания — SPK-C')
        self.assertNotContains(response, 'другой модели XYZ')
        self.spark.refresh_from_db()
        self.assertEqual(self.spark.title, 'Свеча зажигания другой модели XYZ')

    def test_list_shows_placeholder_without_cover(self):
        response = self.client.get(reverse('maintenance_kit_list'))
        self.assertContains(response, 'kit-card-placeholder')
        self.assertContains(response, 'Комплект ТО')
        self.assertContains(response, 'Chery Tiggo 7 Pro')
        self.assertNotContains(response, 'kit-card-cover')

    def test_authenticated_cart_is_atomic(self):
        user = User.objects.create_user(username='buyer-kit', password='secret12345')
        self.client.force_login(user)
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        response = self._add_via_view()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CartItem.objects.filter(user=user).count(), 0)

    def test_list_is_indexable_and_canonical(self):
        response = self.client.get('/maintenance-kits/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="robots" content="index, follow">', html=True)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://zpt.kz/maintenance-kits/">',
            html=True,
        )

    def test_detail_canonical_uses_slug(self):
        response = self.client.get('/maintenance-kits/kit-cart/')
        self.assertContains(
            response,
            '<link rel="canonical" href="https://zpt.kz/maintenance-kits/kit-cart/">',
            html=True,
        )

    def test_sitemap_contains_list_url(self):
        response = self.client.get('/sitemap-static.xml')
        self.assertIn('https://zpt.kz/maintenance-kits/', response.content.decode('utf-8'))

    def test_add_without_sparks_uses_kit_quantities(self):
        response = self._add_via_view(products=(self.air, self.cabin, self.oil))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('orders:cart'), fetch_redirect_response=False)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(self.air.id)], 1)
        self.assertEqual(cart[str(self.cabin.id)], 1)
        self.assertEqual(cart[str(self.oil.id)], 1)
        self.assertNotIn(str(self.spark.id), cart)

    def test_localized_product_id_is_accepted(self):
        from catalog.maintenance_kits import parse_selected_product_ids

        spaced = f'{self.air.id}\xa0'
        parsed = parse_selected_product_ids([spaced, str(self.cabin.id)])
        self.assertEqual(parsed, [self.air.id, self.cabin.id])
        response = self._add_via_view(
            products=(self.air, self.cabin, self.oil),
            extra={'item': [f'{self.air.id}\xa0', str(self.cabin.id), str(self.oil.id)]},
        )
        self.assertEqual(response.status_code, 302)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(self.air.id)], 1)
        self.assertNotIn(str(self.spark.id), cart)

    def test_empty_selection_adds_nothing(self):
        response = self.client.post(
            reverse('maintenance_kit_add_to_cart', kwargs={'slug': self.kit.slug}),
            data={},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_foreign_product_id_adds_nothing(self):
        outsider = _make_product(article='OUT-C', title='Чужой')
        response = self._add_via_view(products=(self.air, outsider))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_client_total_and_quantity_are_ignored(self):
        response = self._add_via_view(
            products=(self.air, self.cabin, self.oil),
            extra={'total': '1', 'price': '1', 'quantity': '99'},
        )
        self.assertEqual(response.status_code, 302)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(self.air.id)], 1)
        self.assertEqual(cart[str(self.oil.id)], 1)
        self.assertNotIn(str(self.spark.id), cart)
        cart_page = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart_page.context['cart_total'], 3000)

    def test_unavailable_selected_item_adds_nothing(self):
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        response = self._add_via_view(products=(self.air, self.oil))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_zero_stock_line_stays_visible_and_kit_stays_in_picker(self):
        self.oil.stock_qty = 0
        self.oil.save(update_fields=['stock_qty'])
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertContains(listing, self.kit.name)
        self.assertNotContains(listing, 'Нет моего автомобиля')
        self.assertContains(listing, 'Часть позиций нет в наличии')
        detail = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertContains(detail, 'Нет в наличии')
        self.assertContains(detail, 'Оставить заявку')
        self.assertContains(detail, '/request-parts/')
        oil_line = next(
            line for line in detail.context['kit_view'].lines
            if line.product.pk == self.oil.id
        )
        self.assertEqual(oil_line.availability, LINE_UNAVAILABLE)
        self.assertFalse(oil_line.selected_by_default)
        response = self._add_via_view(products=(self.air, self.cabin, self.spark))
        cart = self.client.session[SESSION_CART_KEY]
        self.assertNotIn(str(self.oil.id), cart)
        self.assertEqual(cart[str(self.spark.id)], 4)

    def test_unconfirmed_article_hides_invented_price(self):
        self.oil.article = ''
        self.oil.save(update_fields=['article'])
        view = build_kit_view(self.kit)
        oil_line = next(line for line in view.lines if line.product.pk == self.oil.id)
        self.assertEqual(oil_line.availability, LINE_UNCONFIRMED)
        self.assertIsNone(oil_line.unit_price)
        self.assertIsNone(oil_line.subtotal)
        self.assertFalse(oil_line.can_buy)
        response = self.client.get(
            reverse('maintenance_kit_detail', kwargs={'slug': self.kit.slug})
        )
        self.assertContains(response, 'Артикул не подтверждён')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MaintenanceKitMissingCarTests(TestCase):
    def setUp(self):
        country = _make_country()
        brand = Brand.objects.create(country=country, name='Chery')
        model = CarModel.objects.create(brand=brand, name='Tiggo 7 Pro')
        extra_brand = Brand.objects.create(country=country, name='Haval')
        CarModel.objects.create(brand=extra_brand, name='Jolion')
        exeed = Brand.objects.create(country=country, name='Exeed')
        txl = CarModel.objects.create(brand=exeed, name='TXL')
        changan = Brand.objects.create(country=country, name='Changan')
        unik = CarModel.objects.create(brand=changan, name='UNI-K')
        product = _make_product(article='AIR-M')
        self.kit = MaintenanceKit.objects.create(
            name='Комплект ТО Chery Tiggo 7 Pro 1.5T',
            slug='kit-missing-car',
            brand=brand,
            car_model=model,
            engine='1.5T',
            is_active=True,
        )
        MaintenanceKitItem.objects.create(kit=self.kit, product=product, quantity=1)
        self.exeed_kit = MaintenanceKit.objects.create(
            name='Комплект ТО EXEED TXL 1.6T',
            slug='kit-missing-exeed',
            brand=exeed,
            car_model=txl,
            engine='1.6T',
            is_active=True,
        )
        MaintenanceKitItem.objects.create(kit=self.exeed_kit, product=product, quantity=1)
        MaintenanceKit.objects.create(
            name='Комплект ТО Changan UNI-K 2.0T',
            slug='kit-unik-hidden',
            brand=changan,
            car_model=unik,
            engine='2.0T',
            is_active=False,
        )
        self.client = Client()

    def _missing_car_payload(self, **overrides):
        data = {
            'brand': 'Geely',
            'model': 'Coolray',
            'year': '2022',
            'engine': '1.5T',
            'phone': '8 777 232 07 09',
            'vin': '',
        }
        data.update(overrides)
        return data

    def test_picker_lists_only_published_kit_cars(self):
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertContains(listing, 'Chery')
        self.assertContains(listing, 'Tiggo 7 Pro')
        self.assertNotContains(listing, 'Haval')
        self.assertNotContains(listing, 'Jolion')
        self.assertNotContains(listing, 'Нет моего автомобиля')
        self.assertContains(listing, 'q_brand')
        self.assertContains(listing, 'Найти')

    def test_default_list_shows_kits_without_missing_car_prompt(self):
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertContains(listing, self.kit.name)
        self.assertContains(listing, 'Смотреть состав')
        self.assertNotContains(listing, 'По вашему запросу комплект ТО не найден')
        self.assertNotContains(listing, 'Оставить заявку на подбор')

    def test_search_finds_published_chery_kit(self):
        listing = self.client.get(
            reverse('maintenance_kit_list'),
            {'q_brand': 'chery', 'q_model': 'tiggo 7 pro'},
        )
        self.assertContains(listing, self.kit.name)
        self.assertContains(listing, 'Смотреть состав')
        self.assertContains(listing, 'Поиск: chery tiggo 7 pro')
        self.assertContains(listing, 'Сбросить и показать все комплекты')
        self.assertNotContains(listing, 'По вашему запросу комплект ТО не найден')

    def test_search_finds_published_exeed_kit(self):
        listing = self.client.get(
            reverse('maintenance_kit_list'),
            {'q_brand': 'EXEED', 'q_model': 'TXL'},
        )
        self.assertContains(listing, self.exeed_kit.name)
        self.assertContains(listing, 'Смотреть состав')
        self.assertNotContains(listing, self.kit.name)
        self.assertNotContains(listing, 'Комплект ТО Changan UNI-K')

    def test_empty_search_shows_published_kits(self):
        listing = self.client.get(
            reverse('maintenance_kit_list'),
            {'q_brand': '  ', 'q_model': ''},
        )
        self.assertContains(listing, self.kit.name)
        self.assertNotContains(listing, 'По вашему запросу комплект ТО не найден')

    def test_search_missing_model_offers_request_form(self):
        listing = self.client.get(
            reverse('maintenance_kit_list'),
            {'q_brand': 'Haval', 'q_model': 'Jolion'},
        )
        self.assertNotContains(listing, self.kit.name)
        self.assertContains(listing, 'По вашему запросу комплект ТО не найден')
        self.assertContains(listing, 'Оставить заявку на подбор')
        self.assertContains(listing, '/maintenance-kits/no-car/?brand=Haval&amp;model=Jolion')
        self.assertNotContains(listing, 'нет в каталоге')

    def test_search_does_not_reveal_unpublished_draft(self):
        listing = self.client.get(
            reverse('maintenance_kit_list'),
            {'q_brand': 'Changan', 'q_model': 'UNI-K'},
        )
        self.assertNotContains(listing, 'Комплект ТО Changan UNI-K')
        self.assertNotContains(listing, 'kit-unik-hidden')
        self.assertContains(listing, 'По вашему запросу комплект ТО не найден')

    def test_missing_car_form_prefills_brand_and_model(self):
        response = self.client.get(
            reverse('maintenance_kit_missing_car'),
            {'brand': 'Haval', 'model': 'Jolion'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Haval"')
        self.assertContains(response, 'value="Jolion"')
        self.assertContains(response, 'Телефон / WhatsApp')
        self.assertNotContains(response, 'value="77772320709"')

    def test_missing_car_form_saves_demand_without_dispatch(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(vin='LWV12345678901234'),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(request.brand, 'Geely')
        self.assertEqual(request.model, 'Coolray')
        self.assertEqual(request.year, 2022)
        self.assertEqual(request.engine, '1.5T')
        self.assertEqual(request.vin, 'LWV12345678901234')
        self.assertEqual(request.phone, '77772320709')
        self.assertEqual(request.status, MaintenanceKitCarRequest.STATUS_NEW)
        self.assertContains(response, 'Заявка получена')
        self.assertContains(response, 'WhatsApp')
        self.assertNotContains(response, 'комплект уже')
        self.assertNotContains(response, 'в течение')
        self.assertEqual(PartsRequest.objects.count(), 0)
        self.assertEqual(Match.objects.count(), 0)

    def test_phone_is_required_and_normalized(self):
        missing = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(phone=''),
        )
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(MaintenanceKitCarRequest.objects.count(), 0)
        self.assertContains(missing, 'id_phone_error')

        invalid = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(phone='12345'),
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(MaintenanceKitCarRequest.objects.count(), 0)
        self.assertContains(invalid, 'Укажите корректный номер WhatsApp')

        saved = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(phone='+7 (777) 232-07-09'),
        )
        self.assertEqual(saved.status_code, 302)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(request.phone, '77772320709')

    def test_operator_is_notified_with_car_phone_and_admin_link(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(vin='LWV12345678901234'),
        )
        self.assertEqual(response.status_code, 302)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        from catalog.views import FEEDBACK_NOTIFY_EMAIL
        self.assertEqual(message.to, [FEEDBACK_NOTIFY_EMAIL])
        self.assertIn('Geely', message.subject)
        self.assertIn('Coolray', message.body)
        self.assertIn('2022', message.body)
        self.assertIn('1.5T', message.body)
        self.assertIn('77772320709', message.body)
        self.assertIn('LWV12345678901234', message.body)
        self.assertIn(
            reverse(
                'admin:catalog_maintenancekitcarrequest_change',
                args=[request.pk],
            ),
            message.body,
        )

    def test_mail_failure_keeps_saved_request_and_hides_pii_in_logs(self):
        with patch(
            'catalog.maintenance_kit_requests.send_mail',
            side_effect=OSError('smtp down'),
        ):
            with self.assertLogs('catalog.maintenance_kit_requests', level='ERROR') as logs:
                response = self.client.post(
                    reverse('maintenance_kit_missing_car'),
                    data=self._missing_car_payload(vin='LWV12345678901234'),
                )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MaintenanceKitCarRequest.objects.count(), 1)
        combined = '\n'.join(logs.output)
        self.assertIn('Failed to send maintenance kit car request email id=', combined)
        self.assertNotIn('77772320709', combined)
        self.assertNotIn('LWV12345678901234', combined)
        self.assertNotIn('Geely', combined)

    def test_legacy_blank_phone_does_not_break_admin(self):
        row = MaintenanceKitCarRequest.objects.create(
            brand='Haval',
            model='Jolion',
            year=2023,
            engine='1.5T',
        )
        self.assertEqual(row.phone, '')
        self.assertEqual(row.status, MaintenanceKitCarRequest.STATUS_NEW)
        staff = User.objects.create_superuser(
            'kit-admin',
            'kit-admin@test.local',
            'secret12345',
        )
        self.client.force_login(staff)
        listing = self.client.get(
            reverse('admin:catalog_maintenancekitcarrequest_changelist'),
        )
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'Haval')
        self.assertContains(listing, 'Новая')
        change = self.client.get(
            reverse(
                'admin:catalog_maintenancekitcarrequest_change',
                args=[row.pk],
            ),
        )
        self.assertEqual(change.status_code, 200)
        saved = self.client.post(
            reverse(
                'admin:catalog_maintenancekitcarrequest_change',
                args=[row.pk],
            ),
            data={
                'brand': 'Haval',
                'model': 'Jolion',
                'year': '2023',
                'engine': '1.5T',
                'vin': '',
                'phone': '',
                'status': MaintenanceKitCarRequest.STATUS_IN_PROGRESS,
            },
        )
        self.assertEqual(saved.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, MaintenanceKitCarRequest.STATUS_IN_PROGRESS)
        self.assertEqual(row.phone, '')

    def test_vin_is_optional(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(
                brand='Changan',
                model='UNI-K',
                year='2021',
                engine='2.0T',
            ),
        )
        self.assertEqual(response.status_code, 302)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(request.vin, '')
        self.assertEqual(request.brand, 'Changan')
        self.assertEqual(request.phone, '77772320709')

    def test_invalid_year_is_rejected(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data=self._missing_car_payload(year='1901'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MaintenanceKitCarRequest.objects.count(), 0)


class MaintenanceKitCarRequestMigrationTests(TransactionTestCase):
    def test_old_rows_get_new_status_and_blank_phone(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('catalog', '0037_maintenance_kit_car_request')])
        old_apps = executor.loader.project_state(
            [('catalog', '0037_maintenance_kit_car_request')]
        ).apps
        OldRequest = old_apps.get_model('catalog', 'MaintenanceKitCarRequest')
        OldRequest.objects.create(
            brand='Haval',
            model='Jolion',
            year=2023,
            engine='1.5T',
        )
        executor.loader.build_graph()
        executor.migrate([('catalog', '0038_maintenance_kit_car_request_phone_status')])
        new_apps = executor.loader.project_state(
            [('catalog', '0038_maintenance_kit_car_request_phone_status')]
        ).apps
        NewRequest = new_apps.get_model('catalog', 'MaintenanceKitCarRequest')
        row = NewRequest.objects.get()
        self.assertEqual(row.status, 'new')
        self.assertEqual(row.phone, '')
        self.assertFalse(hasattr(OldRequest, 'phone'))


class MaintenanceKitSeedTests(TestCase):
    def setUp(self):
        country = _make_country()
        chery = Brand.objects.create(country=country, name='Chery')
        exeed = Brand.objects.create(country=country, name='Exeed')
        changan = Brand.objects.create(country=country, name='Changan')
        haval = Brand.objects.create(country=country, name='Haval')
        CarModel.objects.create(brand=chery, name='Tiggo 7 Pro')
        CarModel.objects.create(brand=chery, name='Tiggo 7')
        CarModel.objects.create(brand=chery, name='Tiggo 8 Pro')
        CarModel.objects.create(brand=chery, name='Tiggo 8')
        CarModel.objects.create(brand=chery, name='Arrizo 8')
        CarModel.objects.create(brand=exeed, name='TXL')
        CarModel.objects.create(brand=changan, name='UNI-K')
        CarModel.objects.create(brand=changan, name='UNI-V')
        CarModel.objects.create(brand=haval, name='Dargo')
        articles = {
            'T151109111': 'Воздушный Chery',
            'T218107011': 'Салонный Chery',
            '4801012010': 'Масляный Chery',
            'F4J163707010': 'Свеча Chery',
            '151000025AA': 'Воздушный Exeed',
            '301001199AA': 'Салонный Exeed',
            'F4J161012030': 'Масляный Exeed',
            '151000079AA': 'Воздушный Chery 8 Pro',
            '1109190CR01': 'Воздушный UNI-K',
            'CD569F2801032700': 'Салонный UNI-K',
            'D20T0120700': 'Свеча UNI-K',
            'S3010140903': 'Воздушный UNI-V',
            'C281F2801032601': 'Салонный UNI-V',
            '1109101XGW01A': 'Воздушный Dargo',
            '1017110XEN01': 'Масляный Dargo',
        }
        for article, title in articles.items():
            _make_product(article=article, title=title, stock_qty=5)

    def test_apply_creates_published_kits_and_is_idempotent(self):
        apply_maintenance_kits()
        self.assertEqual(MaintenanceKit.objects.count(), 10)
        chery = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
        exeed = MaintenanceKit.objects.get(slug='komplekt-to-exeed-txl-16t')
        univ = MaintenanceKit.objects.get(slug='nabor-to-changan-uni-v-15')
        dargo = MaintenanceKit.objects.get(slug='nabor-to-haval-dargo-20-gw4n20')
        changan = MaintenanceKit.objects.get(slug='komplekt-to-changan-uni-k-20t')
        t8pro = MaintenanceKit.objects.get(slug='nabor-to-chery-tiggo-8-pro-16-sqrf4j16a')
        t7 = MaintenanceKit.objects.get(slug='nabor-to-chery-tiggo-7-15')
        arrizo = MaintenanceKit.objects.get(slug='nabor-to-chery-arrizo-8-16-sqrf4j16c')
        t8c = MaintenanceKit.objects.get(slug='nabor-to-chery-tiggo-8-15t-sqre4t15c')
        t8b = MaintenanceKit.objects.get(slug='nabor-to-chery-tiggo-8-15t-sqre4t15b')
        self.assertTrue(chery.is_active)
        self.assertTrue(exeed.is_active)
        self.assertTrue(univ.is_active)
        self.assertTrue(dargo.is_active)
        self.assertFalse(changan.is_active)
        self.assertTrue(t8pro.is_active)
        self.assertTrue(t7.is_active)
        self.assertTrue(arrizo.is_active)
        self.assertTrue(t8c.is_active)
        self.assertTrue(t8b.is_active)
        self.assertEqual(chery.items.count(), 3)
        self.assertFalse(chery.items.filter(product__article='F4J163707010').exists())
        self.assertEqual(exeed.items.count(), 4)
        self.assertEqual(univ.items.count(), 2)
        self.assertEqual(dargo.items.count(), 2)
        self.assertEqual(changan.items.count(), 1)
        self.assertFalse(changan.items.filter(product__article='D20T0120700').exists())
        self.assertFalse(
            changan.items.filter(product__article='CD569F2801032700').exists()
        )
        self.assertEqual(
            list(changan.items.order_by('id').values_list('product__article', 'quantity')),
            [('1109190CR01', 1)],
        )
        cabin_ref = next(
            line
            for line in changan.reference_lines
            if line.get('type_label') == 'Салонный фильтр'
        )
        self.assertEqual(cabin_ref['article'], 'CD569F2801032700')
        self.assertIn('артикул уточняется', cabin_ref['note'].lower())
        self.assertEqual(t8pro.items.count(), 2)
        self.assertFalse(t8pro.items.filter(product__article='F4J161012030').exists())
        self.assertFalse(t8pro.items.filter(product__article='301001199AA').exists())
        self.assertEqual(t7.items.count(), 3)
        self.assertFalse(t7.items.filter(product__article='F4J163707010').exists())
        self.assertEqual(arrizo.items.count(), 3)
        self.assertFalse(arrizo.items.filter(product__article='F4J163707010').exists())
        self.assertEqual(t8c.items.count(), 3)
        self.assertFalse(t8c.items.filter(product__article='F4J163707010').exists())
        self.assertEqual(t8b.items.count(), 4)
        self.assertIn(UNI_K_INCOMPLETE_WARNING, changan.description)
        self.assertEqual(chery.engine, '1.5T SQRE4T15C')
        self.assertEqual(exeed.engine, '1.6T SQRF4J16A')
        self.assertEqual(chery.cover_note, COVER_SPARKS_EXCLUDED_CAPTION)
        spark_ref = chery.reference_lines[0]
        self.assertEqual(spark_ref['type_label'], 'Свеча зажигания')
        self.assertEqual(spark_ref['article'], '')
        spark_item = exeed.items.get(product__article='F4J163707010')
        self.assertEqual(spark_item.quantity, 4)

        apply_maintenance_kits()
        self.assertEqual(MaintenanceKit.objects.count(), 10)
        self.assertEqual(MaintenanceKitItem.objects.count(), 27)
        chery.refresh_from_db()
        self.assertEqual(chery.items.count(), 3)
        self.assertFalse(chery.items.filter(product__article='F4J163707010').exists())
        self.assertFalse(changan.is_active)

    def test_apply_removes_excluded_spark_and_does_not_restore_it(self):
        apply_maintenance_kits()
        kit = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
        spark = Product.objects.get(article='F4J163707010')
        MaintenanceKitItem.objects.create(kit=kit, product=spark, quantity=4)
        extra = _make_product(article='EXTRA-MANUAL-001', title='Ручная позиция')
        MaintenanceKitItem.objects.create(kit=kit, product=extra, quantity=1)
        self.assertEqual(kit.items.count(), 5)

        apply_maintenance_kits()
        kit.refresh_from_db()
        self.assertEqual(kit.items.count(), 3)
        self.assertFalse(kit.items.filter(product=spark).exists())
        self.assertFalse(kit.items.filter(product=extra).exists())
        self.assertTrue(kit.is_active)
        spark.refresh_from_db()
        self.assertEqual(spark.article, 'F4J163707010')
        self.assertEqual(spark.price, 1000)
        self.assertEqual(spark.stock_qty, 5)

    def test_apply_removes_unconfirmed_uni_k_cabin_and_does_not_restore_it(self):
        apply_maintenance_kits()
        kit = MaintenanceKit.objects.get(slug='komplekt-to-changan-uni-k-20t')
        cabin = Product.objects.get(article='CD569F2801032700')
        MaintenanceKitItem.objects.create(kit=kit, product=cabin, quantity=1)
        self.assertEqual(kit.items.count(), 2)

        apply_maintenance_kits()
        kit.refresh_from_db()
        self.assertEqual(kit.items.count(), 1)
        self.assertFalse(kit.items.filter(product=cabin).exists())
        self.assertFalse(kit.is_active)
        cabin_ref = next(
            line
            for line in kit.reference_lines
            if line.get('type_label') == 'Салонный фильтр'
        )
        self.assertEqual(cabin_ref['article'], 'CD569F2801032700')
        self.assertIn('артикул уточняется', cabin_ref['note'].lower())
        cabin.refresh_from_db()
        self.assertEqual(cabin.price, 1000)
        self.assertEqual(cabin.stock_qty, 5)

    def test_ambiguous_article_skips_kit(self):
        _make_product(article='T151109111-DUP', title='dup')
        Product.objects.filter(article='T151109111-DUP').update(article='T151109111')
        plans = plan_maintenance_kits()
        chery = next(plan for plan in plans if plan.spec['slug'].endswith('15t'))
        self.assertFalse(chery.complete)
        self.assertTrue(any(ref.status == 'AMBIGUOUS' for ref in chery.items))

    def test_missing_article_skips_kit(self):
        Product.objects.filter(article='4801012010').delete()
        plans = plan_maintenance_kits()
        chery = next(plan for plan in plans if plan.spec['slug'].endswith('15t'))
        self.assertFalse(chery.complete)
        self.assertTrue(any(ref.status == 'MISSING' for ref in chery.items))

    def test_command_dry_run_does_not_write(self):
        out = StringIO()
        call_command('seed_maintenance_kits', stdout=out)
        report = out.getvalue()
        self.assertIn('mode: dry-run', report)
        self.assertIn('Набор ТО — 3 позиции Chery Tiggo 7 Pro 1.5T SQRE4T15C', report)
        self.assertIn('Набор ТО — 4 позиции EXEED TXL 1.6T SQRF4J16A', report)
        self.assertIn('Набор ТО — 2 позиции Changan UNI-V 1.5 JL473ZQ7', report)
        self.assertIn('Набор ТО — 2 позиции Haval Dargo 2.0 GW4N20', report)
        self.assertIn('Комплект ТО Changan UNI-K 2.0T', report)
        self.assertIn('would_add: T151109111 x1, T218107011 x1, 4801012010 x1', report)
        self.assertNotIn('F4J163707010', report.split('--- Набор ТО — 3 позиции Chery')[1].split('---')[0])
        self.assertEqual(report.count('result: WOULD create (publish)'), 9)
        self.assertIn('result: WOULD create (draft)', report)
        self.assertEqual(MaintenanceKit.objects.count(), 0)

    def test_changan_draft_is_not_public(self):
        apply_maintenance_kits()
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertNotContains(listing, 'Changan UNI-K')
        self.assertContains(listing, 'Chery Tiggo 7 Pro')
        self.assertContains(listing, 'Набор ТО — 2 позиции Changan UNI-V')
        self.assertContains(listing, 'Набор ТО — 2 позиции Haval Dargo')
        self.assertContains(listing, 'Набор ТО — 2 позиции Chery Tiggo 8 Pro')
        self.assertContains(listing, 'Набор ТО — 3 позиции Chery Tiggo 7 1.5')
        self.assertContains(listing, 'Набор ТО — 3 позиции Chery Arrizo 8')
        self.assertContains(listing, 'Набор ТО — 3 позиции Chery Tiggo 8 1.5T SQRE4T15C')
        self.assertContains(listing, 'Набор ТО — 4 позиции Chery Tiggo 8 1.5T SQRE4T15B')
        response = self.client.get('/maintenance-kits/komplekt-to-changan-uni-k-20t/')
        self.assertEqual(response.status_code, 404)
        post = self.client.post('/maintenance-kits/komplekt-to-changan-uni-k-20t/add-to-cart/')
        self.assertEqual(post.status_code, 404)

    def test_exeed_detail_uses_type_article_and_own_model_copy(self):
        apply_maintenance_kits()
        spark = Product.objects.get(article='F4J163707010')
        spark.title = 'Свеча зажигания Chery Tiggo 7'
        spark.save(update_fields=['title'])
        response = self.client.get('/maintenance-kits/komplekt-to-exeed-txl-16t/')
        html = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Масляный фильтр — F4J161012030', html)
        self.assertIn('Свеча зажигания — F4J163707010', html)
        self.assertNotIn('Свеча зажигания Chery Tiggo 7', html)
        self.assertNotIn('Tiggo 7', html)
        self.assertIn('EXEED TXL 1.6T SQRF4J16A', html)
        self.assertIn('Не для 2.0T', html)
        self.assertNotIn('Комплект расходников для ТО EXEED TXL 1.6T', html)
        spark.refresh_from_db()
        self.assertEqual(spark.title, 'Свеча зажигания Chery Tiggo 7')
        self.assertIn(
            'Комплект ТО Exeed TXL 1.6T SQRF4J16A. Состав, цены и наличие расходников на ZPT.KZ.',
            response.context['page_description'],
        )
        self.assertNotIn('UNI-K', html)
        self.assertNotIn('Changan', html)

    def test_chery_detail_mentions_only_own_model(self):
        apply_maintenance_kits()
        response = self.client.get(
            '/maintenance-kits/komplekt-to-chery-tiggo-7-pro-15t/'
        )
        html = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Масляный фильтр — 4801012010', html)
        self.assertIn('Набор ТО — 3 позиции для Chery Tiggo 7 Pro 1.5T SQRE4T15C', html)
        self.assertIn(COVER_SPARKS_EXCLUDED_CAPTION, html)
        self.assertIn(OEM_UNKNOWN_LABEL, html)
        self.assertIn('Свечи в набор не входят', html)
        self.assertNotIn('F4J163707010', html)
        self.assertNotIn('Свеча зажигания — F4J163707010', html)
        self.assertNotIn('TXL', html)
        self.assertNotIn('UNI-K', html)
        self.assertNotIn('Exeed', html)
        self.assertNotIn('Changan', html)
        self.assertIn(
            'Комплект ТО Chery Tiggo 7 Pro 1.5T SQRE4T15C. Состав, цены и наличие расходников на ZPT.KZ.',
            response.context['page_description'],
        )
        spark = Product.objects.get(article='F4J163707010')
        post = self.client.post(
            '/maintenance-kits/komplekt-to-chery-tiggo-7-pro-15t/add-to-cart/',
            data={'item': [str(spark.id)]},
        )
        self.assertEqual(post.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_partial_kits_cart_excludes_reference_and_sums_selected(self):
        apply_maintenance_kits()
        univ = MaintenanceKit.objects.get(slug='nabor-to-changan-uni-v-15')
        air = Product.objects.get(article='S3010140903')
        cabin = Product.objects.get(article='C281F2801032601')
        view = build_kit_view(univ)
        self.assertEqual(len(view.lines), 2)
        self.assertEqual(len(view.reference_lines), 2)
        self.assertEqual(view.reference_lines[0].article_display, OEM_UNKNOWN_LABEL)
        self.assertEqual(view.total_price, 1000 + 1000)
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertNotContains(listing, 'OEM неизвестен')
        response = self.client.get('/maintenance-kits/nabor-to-changan-uni-v-15/')
        html = response.content.decode('utf-8')
        self.assertIn('OEM неизвестен', html)
        self.assertIn('Справочно', html)
        self.assertNotIn('Купить', html.split('Справочно', 1)[1])
        post = self.client.post(
            '/maintenance-kits/nabor-to-changan-uni-v-15/add-to-cart/',
            data={'item': [str(air.id)]},
        )
        self.assertEqual(post.status_code, 302)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(air.id)], 1)
        self.assertNotIn(str(cabin.id), cart)
        cart_page = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart_page.context['cart_total'], 1000)

    def test_dargo_two_position_kit_keeps_cabin_as_reference(self):
        apply_maintenance_kits()
        response = self.client.get('/maintenance-kits/nabor-to-haval-dargo-20-gw4n20/')
        html = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Воздушный фильтр — 1109101XGW01A', html)
        self.assertIn('Масляный фильтр — 1017110XEN01', html)
        self.assertIn('Салонный фильтр — OEM неизвестен', html)
        self.assertIn('Не для 1.5', html)
        self.assertNotIn('Jolion', html)

    def test_seed_does_not_rewrite_existing_published_kits(self):
        apply_maintenance_kits()
        protected = {
            'komplekt-to-chery-tiggo-7-pro-15t': (
                '1.5T SQRE4T15C',
                ['T151109111', 'T218107011', '4801012010'],
            ),
            'komplekt-to-exeed-txl-16t': (
                '1.6T SQRF4J16A',
                ['151000025AA', '301001199AA', 'F4J161012030', 'F4J163707010'],
            ),
            'nabor-to-changan-uni-v-15': (
                '1.5 JL473ZQ7',
                ['S3010140903', 'C281F2801032601'],
            ),
            'nabor-to-haval-dargo-20-gw4n20': (
                '2.0 GW4N20',
                ['1109101XGW01A', '1017110XEN01'],
            ),
        }
        unik = MaintenanceKit.objects.get(slug='komplekt-to-changan-uni-k-20t')
        self.assertFalse(unik.is_active)
        self.assertFalse(unik.items.filter(product__article='D20T0120700').exists())
        self.assertFalse(unik.items.filter(product__article='CD569F2801032700').exists())
        self.assertEqual(unik.items.count(), 1)
        before = {}
        for slug, (engine, articles) in protected.items():
            kit = MaintenanceKit.objects.get(slug=slug)
            before[slug] = (
                kit.engine,
                kit.is_active,
                list(kit.items.order_by('id').values_list('product__article', 'quantity')),
            )
            self.assertEqual(kit.engine, engine)
            self.assertEqual(
                [article for article, _qty in before[slug][2]],
                articles,
            )
        apply_maintenance_kits()
        unik.refresh_from_db()
        self.assertFalse(unik.is_active)
        for slug, snapshot in before.items():
            kit = MaintenanceKit.objects.get(slug=slug)
            self.assertEqual(kit.engine, snapshot[0])
            self.assertTrue(kit.is_active)
            self.assertEqual(
                list(kit.items.order_by('id').values_list('product__article', 'quantity')),
                snapshot[2],
            )

    def test_tiggo_8_pro_two_position_kit_keeps_disputed_oil_as_reference(self):
        apply_maintenance_kits()
        response = self.client.get(
            '/maintenance-kits/nabor-to-chery-tiggo-8-pro-16-sqrf4j16a/'
        )
        html = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Воздушный фильтр — 151000079AA', html)
        self.assertIn('Салонный фильтр — T218107011', html)
        self.assertIn('Масляный фильтр — OEM неизвестен', html)
        self.assertIn('Свеча зажигания — OEM неизвестен', html)
        self.assertIn('Не для Tiggo 8 без Pro', html)
        self.assertNotIn('F4J161012030', html)
        self.assertNotIn('301001199AA', html)
        self.assertNotIn('F4J163707010', html)
        oil = Product.objects.get(article='F4J161012030')
        post = self.client.post(
            '/maintenance-kits/nabor-to-chery-tiggo-8-pro-16-sqrf4j16a/add-to-cart/',
            data={'item': [str(oil.id)]},
        )
        self.assertEqual(post.status_code, 302)
        self.assertEqual(self.client.session.get(SESSION_CART_KEY, {}), {})

    def test_tiggo_7_kit_is_not_tiggo_7_pro_and_excludes_spark(self):
        apply_maintenance_kits()
        response = self.client.get('/maintenance-kits/nabor-to-chery-tiggo-7-15/')
        html = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Воздушный фильтр — T151109111', html)
        self.assertIn('Салонный фильтр — T218107011', html)
        self.assertIn('Масляный фильтр — 4801012010', html)
        self.assertIn('Не для Tiggo 7 Pro', html)
        self.assertNotIn('F4J163707010', html)
        self.assertNotIn('SQRF4J16A', html)

    def test_arrizo_8_and_tiggo_8_engine_kits_do_not_copy_other_motors(self):
        apply_maintenance_kits()
        arrizo = self.client.get(
            '/maintenance-kits/nabor-to-chery-arrizo-8-16-sqrf4j16c/'
        )
        arrizo_html = arrizo.content.decode('utf-8')
        self.assertEqual(arrizo.status_code, 200)
        self.assertIn('Воздушный фильтр — 151000079AA', arrizo_html)
        self.assertIn('Масляный фильтр — 4801012010', arrizo_html)
        self.assertIn('SQRF4J16C', arrizo_html)
        self.assertIn('Не для Tiggo 8 Pro', arrizo_html)
        self.assertNotIn('F4J161012030', arrizo_html)
        self.assertNotIn('F4J163707010', arrizo_html)

        t8c = self.client.get(
            '/maintenance-kits/nabor-to-chery-tiggo-8-15t-sqre4t15c/'
        )
        t8c_html = t8c.content.decode('utf-8')
        self.assertEqual(t8c.status_code, 200)
        self.assertIn('SQRE4T15C', t8c_html)
        self.assertIn('Не для SQRE4T15B', t8c_html)
        self.assertNotIn('F4J163707010', t8c_html)

        t8b = self.client.get(
            '/maintenance-kits/nabor-to-chery-tiggo-8-15t-sqre4t15b/'
        )
        t8b_html = t8b.content.decode('utf-8')
        self.assertEqual(t8b.status_code, 200)
        self.assertIn('Свеча зажигания — F4J163707010', t8b_html)
        self.assertIn('Не для SQRE4T15C', t8b_html)
        air = Product.objects.get(article='T151109111')
        spark = Product.objects.get(article='F4J163707010')
        post = self.client.post(
            '/maintenance-kits/nabor-to-chery-tiggo-8-15t-sqre4t15b/add-to-cart/',
            data={'item': [str(air.id), str(spark.id)]},
        )
        self.assertEqual(post.status_code, 302)
        cart = self.client.session[SESSION_CART_KEY]
        self.assertEqual(cart[str(air.id)], 1)
        self.assertEqual(cart[str(spark.id)], 4)
        cart_page = self.client.get(reverse('orders:cart'))
        self.assertEqual(cart_page.context['cart_total'], 1000 + 4000)

    def test_type_label_falls_back_to_known_article(self):
        apply_maintenance_kits()
        oil = Product.objects.get(article='F4J161012030')
        oil.title = 'Расходник без указания типа'
        oil.save(update_fields=['title'])
        from catalog.maintenance_kits import kit_component_display_name

        self.assertEqual(
            kit_component_display_name(oil),
            'Масляный фильтр — F4J161012030',
        )
        oil.refresh_from_db()
        self.assertEqual(oil.title, 'Расходник без указания типа')
