from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from io import StringIO

from catalog.commercial import resolve_commercial_price
from catalog.maintenance_kit_seed import (
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
        self.assertContains(listing, 'Нет моего автомобиля')
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


class MaintenanceKitMissingCarTests(TestCase):
    def setUp(self):
        country = _make_country()
        brand = Brand.objects.create(country=country, name='Chery')
        model = CarModel.objects.create(brand=brand, name='Tiggo 7 Pro')
        extra_brand = Brand.objects.create(country=country, name='Haval')
        CarModel.objects.create(brand=extra_brand, name='Jolion')
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
        self.client = Client()

    def test_picker_lists_only_published_kit_cars(self):
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertContains(listing, 'Chery')
        self.assertContains(listing, 'Tiggo 7 Pro')
        self.assertNotContains(listing, 'Haval')
        self.assertNotContains(listing, 'Jolion')

    def test_missing_car_form_saves_demand_without_dispatch(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data={
                'brand': 'Geely',
                'model': 'Coolray',
                'year': '2022',
                'engine': '1.5T',
                'vin': 'LWV12345678901234',
            },
        )
        self.assertEqual(response.status_code, 302)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(request.brand, 'Geely')
        self.assertEqual(request.model, 'Coolray')
        self.assertEqual(request.year, 2022)
        self.assertEqual(request.engine, '1.5T')
        self.assertEqual(request.vin, 'LWV12345678901234')

    def test_vin_is_optional(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data={
                'brand': 'Changan',
                'model': 'UNI-K',
                'year': '2021',
                'engine': '2.0T',
            },
        )
        self.assertEqual(response.status_code, 302)
        request = MaintenanceKitCarRequest.objects.get()
        self.assertEqual(request.vin, '')
        self.assertEqual(request.brand, 'Changan')

    def test_invalid_year_is_rejected(self):
        response = self.client.post(
            reverse('maintenance_kit_missing_car'),
            data={
                'brand': 'Geely',
                'model': 'Coolray',
                'year': '1901',
                'engine': '1.5T',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MaintenanceKitCarRequest.objects.count(), 0)


class MaintenanceKitSeedTests(TestCase):
    def setUp(self):
        country = _make_country()
        chery = Brand.objects.create(country=country, name='Chery')
        exeed = Brand.objects.create(country=country, name='Exeed')
        changan = Brand.objects.create(country=country, name='Changan')
        CarModel.objects.create(brand=chery, name='Tiggo 7 Pro')
        CarModel.objects.create(brand=exeed, name='TXL')
        CarModel.objects.create(brand=changan, name='UNI-K')
        articles = {
            'T151109111': 'Воздушный Chery',
            'T218107011': 'Салонный Chery',
            '4801012010': 'Масляный Chery',
            'F4J163707010': 'Свеча Chery',
            '151000025AA': 'Воздушный Exeed',
            '301001199AA': 'Салонный Exeed',
            'F4J161012030': 'Масляный Exeed',
            '1109190CR01': 'Воздушный UNI-K',
            'CD569F2801032700': 'Салонный UNI-K',
            'D20T0120700': 'Свеча UNI-K',
        }
        for article, title in articles.items():
            _make_product(article=article, title=title, stock_qty=5)

    def test_apply_creates_three_kits_and_is_idempotent(self):
        apply_maintenance_kits()
        self.assertEqual(MaintenanceKit.objects.count(), 3)
        chery = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
        exeed = MaintenanceKit.objects.get(slug='komplekt-to-exeed-txl-16t')
        changan = MaintenanceKit.objects.get(slug='komplekt-to-changan-uni-k-20t')
        self.assertTrue(chery.is_active)
        self.assertTrue(exeed.is_active)
        self.assertFalse(changan.is_active)
        self.assertEqual(chery.items.count(), 4)
        self.assertEqual(exeed.items.count(), 4)
        self.assertEqual(changan.items.count(), 3)
        self.assertIn(UNI_K_INCOMPLETE_WARNING, changan.description)
        spark_item = chery.items.get(product__article='F4J163707010')
        self.assertEqual(spark_item.quantity, 4)

        apply_maintenance_kits()
        self.assertEqual(MaintenanceKit.objects.count(), 3)
        self.assertEqual(MaintenanceKitItem.objects.count(), 11)

    def test_apply_does_not_delete_extra_manual_items(self):
        apply_maintenance_kits()
        kit = MaintenanceKit.objects.get(slug='komplekt-to-chery-tiggo-7-pro-15t')
        extra = _make_product(article='EXTRA-MANUAL-001', title='Ручная позиция')
        MaintenanceKitItem.objects.create(kit=kit, product=extra, quantity=1)
        self.assertEqual(kit.items.count(), 5)

        apply_maintenance_kits()
        kit.refresh_from_db()
        self.assertEqual(kit.items.count(), 5)
        self.assertTrue(kit.items.filter(product=extra).exists())
        self.assertTrue(kit.is_active)

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
        self.assertIn('Комплект ТО Chery Tiggo 7 Pro 1.5T', report)
        self.assertIn('Комплект ТО EXEED TXL 1.6T', report)
        self.assertIn('Комплект ТО Changan UNI-K 2.0T', report)
        self.assertEqual(report.count('result: WOULD create (publish)'), 2)
        self.assertIn('result: WOULD create (draft)', report)
        self.assertEqual(MaintenanceKit.objects.count(), 0)

    def test_changan_draft_is_not_public(self):
        apply_maintenance_kits()
        listing = self.client.get(reverse('maintenance_kit_list'))
        self.assertNotContains(listing, 'Changan UNI-K')
        self.assertContains(listing, 'Chery Tiggo 7 Pro')
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
        self.assertIn('EXEED TXL 1.6T', html)
        self.assertIn('Комплект расходников для ТО EXEED TXL 1.6T', html)
        spark.refresh_from_db()
        self.assertEqual(spark.title, 'Свеча зажигания Chery Tiggo 7')
        self.assertIn(
            'Комплект ТО Exeed TXL 1.6T. Состав, цены и наличие расходников на ZPT.KZ.',
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
        self.assertIn('Комплект расходников для ТО Chery Tiggo 7 Pro 1.5T', html)
        self.assertNotIn('TXL', html)
        self.assertNotIn('UNI-K', html)
        self.assertNotIn('Exeed', html)
        self.assertNotIn('Changan', html)
        self.assertIn(
            'Комплект ТО Chery Tiggo 7 Pro 1.5T. Состав, цены и наличие расходников на ZPT.KZ.',
            response.context['page_description'],
        )

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
