from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from catalog.models import Product
from integrations.kaspi_pay import KaspiPayClient

from .cart import CartManager
from .constants import DEFAULT_WAREHOUSE_ADDRESS, TRANSPORT_COMPANIES
from .forms import CheckoutForm
from .models import Order, OrderItem, KaspiTransaction


def _warehouse_address():
    from django.conf import settings
    return getattr(settings, 'ZPT_WAREHOUSE_ADDRESS', DEFAULT_WAREHOUSE_ADDRESS)


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


@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(Product, pk=product_id, status='active')
    quantity = max(1, int(request.POST.get('quantity', 1)))
    CartManager(request).add(product.id, quantity)
    messages.success(request, f'«{product.title}» добавлен в корзину.')
    next_url = request.POST.get('next') or product.get_absolute_url()
    return redirect(next_url)


@require_POST
def cart_remove(request, product_id):
    CartManager(request).remove(product_id)
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
            payment_url = KaspiPayClient().create_payment_ticket(order)
            return redirect(payment_url)
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
def mock_kaspi_payment(request, order_id):
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

    return render(request, 'orders/mock_kaspi_payment.html', {
        'order': order,
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
