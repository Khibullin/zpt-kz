import json

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
    cart = CartManager(request)
    quantity = max(1, int(request.POST.get('quantity', 1)))
    wants_json = _wants_json(request)

    if product_id is None:
        product_id = request.POST.get('product_id')

    if not product_id:
        message = 'product_id is required'
        if wants_json:
            return JsonResponse({'ok': False, 'message': message}, status=400)
        messages.error(request, message)
        return redirect('catalog_list')

    try:
        product = Product.objects.get(pk=product_id, status='active')
    except (Product.DoesNotExist, ValueError, TypeError):
        message = 'Товар не найден'
        if wants_json:
            return JsonResponse({'ok': False, 'message': message}, status=404)
        messages.error(request, message)
        return redirect('catalog_list')

    cart.add(product.id, quantity)
    message = f'«{product.title}» добавлен в корзину.'

    if wants_json:
        return JsonResponse(_cart_json(cart, message))

    messages.success(request, message)
    next_url = request.POST.get('next') or product.get_absolute_url()
    return redirect(next_url)


@require_POST
def cart_add_virtual(request):
    cart = CartManager(request)

    try:
        if request.content_type == 'application/json':
            payload = json.loads(request.body.decode('utf-8'))
        else:
            payload = request.POST.dict()
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'Invalid JSON'}, status=400)

    quantity = max(1, int(payload.get('quantity', 1)))

    try:
        product = CartManager.get_or_create_virtual_product(payload)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)

    cart.add(product.id, quantity)
    message = f'«{product.title}» добавлен в корзину.'

    response = _cart_json(cart, message)
    response['product_id'] = product.id
    return JsonResponse(response)


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
