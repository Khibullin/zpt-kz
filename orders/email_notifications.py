import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .constants import DEFAULT_WAREHOUSE_ADDRESS
from .models import Order

logger = logging.getLogger(__name__)


def get_order_admin_email():
    order_admin_email = getattr(settings, 'ORDER_ADMIN_EMAIL', '') or ''
    order_admin_email = str(order_admin_email).strip()
    if order_admin_email:
        return order_admin_email
    return str(getattr(settings, 'EMAIL_HOST_USER', '') or '').strip()


def format_price_kzt(value):
    return f'{int(value):,}'.replace(',', ' ')


def normalize_phone_for_wa_me(phone):
    digits = re.sub(r'\D', '', str(phone or ''))
    if digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    if digits.startswith('7'):
        return digits
    return digits.lstrip('+')


def build_admin_order_url(order):
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
    path = reverse('admin:orders_order_change', args=[order.pk])
    return f'{base}{path}'


def build_buyer_whatsapp_url(phone):
    digits = normalize_phone_for_wa_me(phone)
    if not digits:
        return ''
    return f'https://wa.me/{digits}'


def format_delivery_block(order):
    warehouse_address = getattr(
        settings,
        'ZPT_WAREHOUSE_ADDRESS',
        DEFAULT_WAREHOUSE_ADDRESS,
    )
    lines = [order.delivery_method_label]

    address = order.delivery_address or {}
    if order.delivery_method == Order.DELIVERY_PICKUP:
        lines.append(warehouse_address)
    elif order.delivery_method == Order.DELIVERY_COURIER:
        street = address.get('street', '')
        house = address.get('house', '')
        apartment = address.get('apartment', '')
        courier_line = f'{street}, д. {house}'.strip(', ')
        if apartment:
            courier_line = f'{courier_line}, кв. {apartment}'
        lines.append(courier_line)
    elif order.delivery_method == Order.DELIVERY_KZ:
        city = address.get('city', '')
        transport_label = address.get('transport_company_label') or address.get('transport_company', '')
        lines.append(f'{city}, {transport_label}'.strip(', '))

    return '\n'.join(line for line in lines if line)


def build_order_email_body(order):
    lines = [
        f'Новый заказ ZPT.KZ №{order.id}',
        '',
        'Продавец:',
        order.seller_name or '—',
        f'WhatsApp продавца: {order.seller_whatsapp or "—"}',
        '',
        'Покупатель:',
        order.customer_name,
        f'Телефон: {order.customer_phone}',
        '',
        'Доставка:',
        format_delivery_block(order),
        '',
        'Товары:',
    ]

    for index, item in enumerate(order.items.all(), start=1):
        article = item.product.article or '—'
        line_total = item.price_at_purchase * item.quantity
        lines.extend([
            f'{index}. {item.product.title}',
            f'Артикул: {article}',
            f'Количество: {item.quantity}',
            f'Цена: {format_price_kzt(item.price_at_purchase)} ₸',
            f'Сумма: {format_price_kzt(line_total)} ₸',
            '',
        ])

    lines.extend([
        'Итого:',
        f'{format_price_kzt(order.total_price)} ₸',
        '',
    ])

    buyer_wa_url = build_buyer_whatsapp_url(order.customer_phone)
    if buyer_wa_url:
        lines.extend([
            'Написать покупателю в WhatsApp:',
            buyer_wa_url,
            '',
        ])

    lines.extend([
        'Открыть заказ в Django Admin:',
        build_admin_order_url(order),
    ])

    return '\n'.join(lines)


def send_order_admin_email(order_id):
    recipient = get_order_admin_email()
    if not recipient:
        logger.warning(
            'ORDER_ADMIN_EMAIL and EMAIL_HOST_USER are empty; '
            'skip admin email for order #%s',
            order_id,
        )
        return False

    try:
        order = Order.objects.prefetch_related('items__product').get(pk=order_id)
    except Order.DoesNotExist:
        logger.error('Order #%s not found for admin email', order_id)
        return False

    subject = (
        f'Новый заказ ZPT.KZ №{order.id} — '
        f'{order.seller_name or "—"} — '
        f'{format_price_kzt(order.total_price)} ₸'
    )
    body = build_order_email_body(order)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('Failed to send admin email for order #%s', order_id)
        return False
