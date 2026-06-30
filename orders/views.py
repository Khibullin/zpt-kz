import json
import traceback

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


def _read_cart_add_payload(request, path_product_id=None):
    """Read cart add payload from JSON body or form POST."""
    content_type = request.content_type or ''
    if request.body and 'application/json' in content_type:
        data = json.loads(request.body.decode('utf-8') or '{}')
        if not isinstance(data, dict):
            raise ValueError('Invalid JSON payload')
        return data

    data = request.POST.dict() if request.POST else {}
    product_data_raw = data.get('product_data')
    if isinstance(product_data_raw, str) and product_data_raw.strip():
        try:
            data['product_data'] = json.loads(product_data_raw)
        except json.JSONDecodeError:
            pass

    if path_product_id is not None:
        data['product_id'] = path_product_id
    return data


@require_POST
def api_cart_add(request, product_id=None):
    try:
        data = _read_cart_add_payload(request, path_product_id=product_id)
        print('--- DEBUG: Пришли данные в корзину:', data)

        product_id = data.get('product_id')
        quantity = int(data.get('quantity', 1))

        cart_manager = CartManager(request)

        if product_id:
            product = Product.objects.filter(id=product_id).first()
            if not product:
                return JsonResponse(
                    {
                        'success': False,
                        'ok': False,
                        'error': 'Товар не найден в базе данных',
                        'message': 'Товар не найден в базе данных',
                    },
                    status=404,
                )

            cart_manager.add(product_id=product.id, quantity=quantity)
        else:
            product_data = data.get('product_data')
            if not product_data:
                sku = str(data.get('sku') or data.get('article') or '').strip()
                brand = str(data.get('brand') or '').strip()
                if sku and brand:
                    product_data = data
            if not product_data:
                return JsonResponse(
                    {
                        'success': False,
                        'ok': False,
                        'error': 'Нет данных о товаре',
                        'message': 'Нет данных о товаре',
                    },
                    status=400,
                )

            product = cart_manager.get_or_create_virtual_product(product_data)
            cart_manager.add(product_id=product.id, quantity=quantity)

        total_items = cart_manager.get_total_items()
        return JsonResponse({
            'success': True,
            'ok': True,
            'message': 'Товар добавлен',
            'total_items': total_items,
            'cart_count': total_items,
            'cart_total': cart_manager.get_total(),
            'product_id': product.id,
        })

    except Exception as e:
        print('--- CRITICAL ERROR IN CART_ADD ---')
        traceback.print_exc()
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': str(e),
                'message': str(e),
            },
            status=500,
        )


@require_POST
def cart_add(request, product_id=None):
    return api_cart_add(request, product_id=product_id)


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
def cart_add_virtual(request):
    """Backward-compatible alias for virtual products via /cart/add/virtual/."""
    return api_cart_add(request)


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
