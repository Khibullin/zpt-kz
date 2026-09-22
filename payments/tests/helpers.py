from django.contrib.auth.models import User

from catalog.models import Product, SellerProfile
from orders.models import Order, OrderItem

PASS1 = 'stage1_pass1'
PASS2 = 'stage1_pass2'

ENABLED_SETTINGS = {
    'ROBOKASSA_ENABLED': True,
    'ROBOKASSA_TEST_ENABLED': True,
    'ROBOKASSA_LIVE_ENABLED': False,
    'ROBOKASSA_MERCHANT_LOGIN': 'zptkz',
    'ROBOKASSA_HASH_ALGO_TEST': 'sha256',
    'ROBOKASSA_PASS1_TEST': PASS1,
    'ROBOKASSA_PASS2_TEST': PASS2,
}


def make_users():
    superuser = User.objects.create_superuser(
        'rbk-super',
        'super@example.com',
        'secret12345',
    )
    staff = User.objects.create_user(
        'rbk-staff',
        password='secret12345',
        is_staff=True,
        is_superuser=False,
    )
    plain = User.objects.create_user('rbk-user', password='secret12345')
    return superuser, staff, plain


def make_seller(username, name='Own Seller', phone='77001110001'):
    user = User.objects.create_user(username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def make_product(seller, article, **kwargs):
    defaults = {
        'title': f'Part {article}',
        'article': article,
        'price': 1000,
        'seller_name': seller.name if seller else 'Unbound',
        'whatsapp_number': seller.phone if seller else '77000000000',
        'seller_profile': seller,
        'status': 'active',
        'city': 'Алматы',
        'stock_qty': 7,
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def make_order(products_with_qty, *, status=Order.STATUS_NEW, total=None):
    items = []
    computed = 0
    for product, quantity in products_with_qty:
        computed += int(product.price) * int(quantity)
        items.append((product, quantity, int(product.price)))
    order = Order.objects.create(
        customer_name='Тест Покупатель',
        customer_phone='+77015550000',
        seller_name=items[0][0].seller_name,
        seller_whatsapp=items[0][0].whatsapp_number,
        status=status,
        total_price=computed if total is None else total,
        delivery_method=Order.DELIVERY_PICKUP,
        delivery_address={'type': 'pickup', 'address': 'Алматы'},
    )
    OrderItem.objects.bulk_create([
        OrderItem(
            order=order,
            product=product,
            quantity=quantity,
            price_at_purchase=price,
        )
        for product, quantity, price in items
    ])
    return order


def enabled_settings(seller):
    data = dict(ENABLED_SETTINGS)
    data['ROBOKASSA_OWN_SELLER_PROFILE_ID'] = str(seller.pk)
    return data
