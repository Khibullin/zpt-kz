import json
import logging

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.models import Product
from catalog.templatetags.phone_extras import format_phone
from integrations.kaspi_pay import KaspiPayClient

from .cart import CartManager
from .constants import DEFAULT_WAREHOUSE_ADDRESS, TRANSPORT_COMPANIES
from .forms import CheckoutForm
from .models import Order, OrderItem, KaspiTransaction

logger = logging.getLogger(__name__)


def _warehouse_address():
    from django.conf import settings
    return getattr(settings, 'ZPT_WAREHOUSE_ADDRESS', DEFAULT_WAREHOUSE_ADDRESS)


def _wants_json(request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.headers.get('Accept', '')
    return 'application/json' in accept


def _cart_json(cart, message=''):
    return {
        'ok': True,
        'message': message,
        'cart_count': cart.get_count(),
        'cart_total': cart.get_total(),
    }


def _cart_error(message, wants_json, request, status=400):
    if wants_json:
        return JsonResponse({'ok': False, 'message': message}, status=status)
    messages.error(request, message)
    return redirect('catalog_list')


def _parse_json_field(raw_value):
    if raw_value is None or raw_value == '':
        return None
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _looks_like_virtual_payload(payload):
    if not isinstance(payload, dict):
        return False
    sku = str(payload.get('sku') or payload.get('article') or '').strip()
    brand = str(payload.get('brand') or '').strip()
    return bool(sku and brand)


def _parse_cart_add_request(request, path_product_id=None):
    """
    Normalize cart add input from JSON body, form POST, or URL path.
    Returns (raw_product_id, product_data, quantity).
    """
    raw_product_id = path_product_id
    product_data = None
    quantity = 1
    payload = {}

    content_type = request.content_type or ''
    if 'application/json' in content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            raise ValueError('Invalid JSON')
        if not isinstance(payload, dict):
            raise ValueError('Invalid JSON payload')

        raw_product_id = raw_product_id if raw_product_id is not None else payload.get('product_id')
        product_data = _parse_json_field(payload.get('product_data'))
        if product_data is None and _looks_like_virtual_payload(payload):
            product_data = payload
        quantity = payload.get('quantity', 1)
    else:
        if raw_product_id is None:
            raw_product_id = request.POST.get('product_id')
        product_data = _parse_json_field(request.POST.get('product_data'))
        if product_data is None and _looks_like_virtual_payload(request.POST.dict()):
            product_data = request.POST.dict()
        quantity = request.POST.get('quantity', 1)

    try:
        quantity = max(1, int(quantity))
    except (TypeError, ValueError):
        quantity = 1

    return raw_product_id, product_data, quantity


def _resolve_cart_product(raw_product_id, product_data):
    """
    Resolve a Product from a local id or virtual supplier payload.
    Returns (product, error_message).
    """
    if raw_product_id is not None and str(raw_product_id).strip() != '':
        try:
            product_id_int = int(str(raw_product_id).strip())
        except (TypeError, ValueError):
            logger.error('cart_add: invalid product_id=%r', raw_product_id)
            return None, 'Товар не найден'

        product = Product.objects.filter(id=product_id_int).first()
        if product is None:
            logger.error(
                'cart_add: local product not found id=%s',
                product_id_int,
            )
            return None, f'Товар с id={product_id_int} не найден'

        return product, None

    if product_data:
        sku = str(product_data.get('sku') or product_data.get('article') or '').strip()
        brand = str(product_data.get('brand') or '').strip()
        if not sku or not brand:
            return None, 'product_data requires sku and brand'

        try:
            product = CartManager.get_or_create_virtual_product(product_data)
        except ValueError as exc:
            logger.error('cart_add: virtual product error=%s data=%r', exc, product_data)
            return None, str(exc)

        return product, None

    return None, 'Укажите product_id или product_data'


def cart_view(request):
    cart = CartManager(request)
    cart.prune_invalid()
    items = cart.get_items()

    return render(request, 'orders/cart.html', {
        'items': items,
        'cart_total': cart.get_total(),
        'cart_count': cart.get_count(),
        'warehouse_address': _warehouse_address(),
    })


@require_GET
def cart_count_api(request):
    cart = CartManager(request)
    return JsonResponse({
        'ok': True,
        'cart_count': cart.get_count(),
        'cart_total': cart.get_total(),
    })


@require_POST
def cart_add(request, product_id=None):
    """Universal cart add: local product_id or virtual product_data."""
    cart = CartManager(request)
    wants_json = _wants_json(request)

    logger.error(
        'cart_add: path_product_id=%r content_type=%r POST=%r body=%r',
        product_id,
        request.content_type,
        request.POST.dict() if request.POST else {},
        (request.body[:500].decode('utf-8', errors='replace') if request.body else ''),
    )

    try:
        raw_product_id, product_data, quantity = _parse_cart_add_request(
            request,
            path_product_id=product_id,
        )
    except ValueError as exc:
        logger.error('cart_add: parse error=%s', exc)
        return _cart_error(str(exc), wants_json, request, status=400)

    logger.error(
        'cart_add: parsed product_id=%r product_data=%r quantity=%s',
        raw_product_id,
        product_data,
        quantity,
    )

    product, error_message = _resolve_cart_product(raw_product_id, product_data)
    if product is None:
        status = 404 if 'не найден' in (error_message or '') else 400
        return _cart_error(error_message, wants_json, request, status=status)

    cart.add(product.id, quantity)
    message = f'«{product.title}» добавлен в корзину.'

    if wants_json:
        response = _cart_json(cart, message)
        response['product_id'] = product.id
        return JsonResponse(response)

    messages.success(request, message)
    next_url = request.POST.get('next') or product.get_absolute_url()
    return redirect(next_url)


@require_POST
def cart_add_virtual(request):
    """Backward-compatible alias for virtual products via /cart/add/virtual/."""
    return cart_add(request)


@require_POST
def cart_remove(request, product_id):
    cart = CartManager(request)
    cart.remove(product_id)

    if _wants_json(request):
        return JsonResponse(_cart_json(cart, 'Товар удалён из корзины.'))

    messages.info(request, 'Товар удалён из корзины.')
    return redirect('orders:cart')


@require_http_methods(['GET', 'POST'])
def checkout(request):
    cart = CartManager(request)
    cart.prune_invalid()
    items = cart.get_items()

    if not items:
        messages.warning(request, 'Корзина пуста. Добавьте товары перед оформлением заказа.')
        return redirect('catalog_list')

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    customer_name=form.cleaned_data['customer_name'],
                    customer_phone=form.cleaned_data['customer_phone'],
                    delivery_method=form.cleaned_data['delivery_method'],
                    delivery_address=form.build_delivery_address(),
                    total_price=cart.get_total(),
                    status=Order.STATUS_PENDING_PAYMENT,
                )
                OrderItem.objects.bulk_create([
                    OrderItem(
                        order=order,
                        product=item['product'],
                        quantity=item['quantity'],
                        price_at_purchase=item['product'].price,
                    )
                    for item in items
                ])

            cart.clear()
            KaspiPayClient().create_invoice(order)
            return redirect('orders:order_payment', order_id=order.pk)
    else:
        initial = {}
        if request.user.is_authenticated:
            profile = getattr(request.user, 'seller_profile', None)
            if profile:
                initial['customer_name'] = profile.name
                initial['customer_phone'] = profile.phone
        form = CheckoutForm(initial=initial)

    return render(request, 'orders/checkout.html', {
        'form': form,
        'items': items,
        'cart_total': cart.get_total(),
        'warehouse_address': _warehouse_address(),
        'transport_companies': TRANSPORT_COMPANIES,
    })


@require_http_methods(['GET', 'POST'])
def order_payment(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    if order.status == Order.STATUS_PAID:
        return redirect('orders:order_success', order_id=order.pk)

    if request.method == 'POST':
        client = KaspiPayClient()
        payload = client.build_mock_success_payload(order)

        with transaction.atomic():
            order.status = Order.STATUS_PAID
            order.save(update_fields=['status', 'updated_at'])
            KaspiTransaction.objects.create(
                order=order,
                kaspi_id=payload['transaction_id'],
                status='SUCCESS',
                raw_response=payload,
            )

        messages.success(request, 'Оплата прошла успешно. Заказ передан на сборку.')
        return redirect('orders:order_success', order_id=order.pk)

    formatted_phone = format_phone(order.customer_phone)
    return render(request, 'orders/order_payment.html', {
        'order': order,
        'formatted_phone': formatted_phone,
    })


def order_success(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'),
        pk=order_id,
    )
    return render(request, 'orders/order_success.html', {
        'order': order,
        'warehouse_address': _warehouse_address(),
    })
