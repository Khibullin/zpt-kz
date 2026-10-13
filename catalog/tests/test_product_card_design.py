"""Keep purchase eligibility and links intact when presenting approved cards."""
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase

from catalog.models import Product, ProductKaspiListing, SellerProfile
from catalog.templatetags.product_extras import public_card_title
from catalog.wholesale import attach_public_wholesale_flags


class ProductCardDesignTests(TestCase):
    def make_product(self, **overrides):
        fields = dict(title='Фильтр JAC — ABC-123', article='ABC-123',
                      price=1590, status='active', seller_name='Shop',
                      whatsapp_number='+77001234567')
        fields.update(overrides)
        product = Product.objects.create(**fields)
        product.seller = product.seller_profile
        attach_public_wholesale_flags([product])
        return product

    def render_card(self, product, compact=True):
        template = 'catalog/interface/product_card.html' if compact else 'catalog/includes/product_card.html'
        return render_to_string(template, {'product': product})

    def test_retail_uses_existing_cart_and_does_not_invent_stock(self):
        product = self.make_product()
        html = self.render_card(product)
        self.assertIn('data-cart-add', html)
        self.assertIn('data-cart-mode="retail"', html)
        self.assertIn('В корзину', html)
        self.assertNotIn('product-card-stock--in', html)
        self.assertEqual(public_card_title(product), 'Фильтр JAC')
        product.refresh_from_db()
        self.assertEqual(product.title, 'Фильтр JAC — ABC-123')
        self.assertIsNone(product.stock_qty)

    def test_sold_out_is_neutral_and_has_no_cart(self):
        product = self.make_product(stock_qty=0)
        for compact in (True, False):
            html = self.render_card(product, compact)
            self.assertIn('product-card-stock--out', html)
            self.assertIn('Нет в наличии', html)
            self.assertIn('product-detail-link', html)
            self.assertNotIn('data-cart-add', html)

    def test_verified_kaspi_url_is_preserved_independent_of_local_stock(self):
        product = self.make_product(stock_qty=0)
        url = 'https://kaspi.kz/shop/p/test-filter-12345678/'
        ProductKaspiListing.objects.create(product=product, master_sku='12345678', public_url=url)
        html = self.render_card(product)
        self.assertIn(url, html)
        self.assertIn('Купить на Kaspi', html)
        self.assertNotIn('data-cart-add', html)

    def test_missing_ag_parts_link_opens_details_without_new_checkout(self):
        seller = SellerProfile.objects.create(
            user=get_user_model().objects.create_user(username='card-ag'),
            name='AG Parts', slug='ag-parts', phone='+77001234567')
        product = self.make_product(seller_profile=seller)
        html = self.render_card(product)
        self.assertIn('product-detail-link', html)
        self.assertNotIn('data-cart-add', html)
        self.assertNotIn('Купить на Kaspi', html)
        self.assertNotIn('Переход на Kaspi для этого товара уточняется', html)
        detail_controls = render_to_string('catalog/includes/product_buy_controls.html', {'product': product})
        self.assertIn('Переход на Kaspi для этого товара уточняется', detail_controls)

    def test_request_price_keeps_contact_route_and_falls_back_without_phone(self):
        product = self.make_product(price=None, price_on_request=True)
        html = self.render_card(product)
        self.assertIn('Узнать цену', html)
        self.assertIn('?src=card', html)
        self.assertNotIn('data-cart-add', html)
        product.whatsapp_number = 'invalid'
        self.assertIn('product-detail-link', self.render_card(product))

    def test_real_image_and_article_survive_with_safe_missing_image_placeholder(self):
        product = self.make_product(main_image='products/original.jpg')
        html = self.render_card(product)
        self.assertIn('products/original.jpg', html)
        self.assertIn('ABC-123', html)
        self.assertIn('product-card-placeholder" hidden', html)
        product.main_image = ''
        html = self.render_card(product)
        self.assertIn('Фото уточняется', html)
        self.assertNotIn('images/logo.png', html)
