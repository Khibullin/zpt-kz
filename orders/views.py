import json
import logging
import traceback

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.commercial import get_request_seller_profile, resolve_commercial_price
from catalog.models import Product
from catalog.wholesale import (
    build_wholesale_terms_snapshot,
    public_stock_status,
    quote_public_wholesale,
    remaining_wholesale_qty,
    resolve_wholesale_owner,
    wholesale_cart_condition_lines,
    wholesale_checkout_presentation,
    wholesale_success_presentation,
)

from .attribution import clear_utm, get_utm_snapshot
from .cart import CartManager
from .constants import (
    CART_MODE_CONFLICT,
    DEFAULT_WAREHOUSE_ADDRESS,
    TRANSPORT_COMPANIES,
)
from .email_notifications import build_whatsapp_url, send_order_admin_email
from .forms import CheckoutForm
from .models import Order, OrderItem
from .seller_utils import (
    CartModeConflictError,
    CartSellerConflictError,
    get_order_pickup_display_address,
    get_seller_snapshot_from_items,
    resolve_pickup_options,
    resolve_seller_profile_from_order,
)

logger = logging.getLogger(__name__)

UNAVAILABLE_CART_MESSAGE = 'Товар больше недоступен и был удалён из корзины.'


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


def _format_price_kzt(value):
    return f'{int(value):,}'.replace(',', ' ')


def _mode_conflict_response(message):
    return JsonResponse(
        {
            'success': False,
            'ok': False,
            'error': CART_MODE_CONFLICT,
            'code': CART_MODE_CONFLICT,
            'message': message,
        },
        status=409,
    )


def _wholesale_minimum_message(status):
    remaining = status.get('remaining') or 0
    minimum = status.get('minimum') or 0
    return (
        f'Добавьте ещё {remaining} шт. для оптового заказа. '
        f'Минимум — {minimum} единиц в ассортименте.'
    )


def _seller_conflict_response(seller_name):
    message = (
        f'В корзине уже есть товары продавца «{seller_name}». '
        f'Сначала оформите текущий заказ или очистите корзину.'
    )
    return JsonResponse(
        {
            'success': False,
            'ok': False,
            'error': 'В корзине уже есть товары другого продавца.',
            'message': message,
        },
        status=409,
    )


def _read_cart_add_payload(request, path_product_id=None):
    """Read cart add payload from JSON body or form POST."""
    content_type = request.content_type or ''
    if request.body and 'application/json' in content_type:
        data = json.loads(request.body.decode('utf-8') or '{}')
        if not isinstance(data, dict):
            raise ValueError('Invalid JSON payload')
    else:
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


def _find_local_product(product_id=None, article=None, supplier=None):
    """
    Resolve a catalog product by primary key, with article fallback for
    environments where rendered page IDs may not match the server DB.

    Only active products can enter or update the cart.
    """
    article_str = str(article or '').strip()
    supplier_str = str(supplier or '').strip() or Product.SUPPLIER_LOCAL
    active = Product.objects.filter(status='active')

    if product_id is not None and str(product_id).strip() != '':
        try:
            pk = int(product_id)
        except (TypeError, ValueError):
            pk = None

        if pk is not None:
            product = active.filter(pk=pk).first()
            if product:
                return product, 'pk'

    if article_str:
        product = active.filter(
            article=article_str,
            supplier=supplier_str,
        ).first()
        if product:
            return product, 'article'

    return None, None


def _prune_unavailable_cart_items(request, cart):
    removed = cart.prune_invalid()
    if removed:
        messages.warning(request, UNAVAILABLE_CART_MESSAGE)
    return removed


@require_POST
def api_cart_add(request, product_id=None):
    try:
        data = _read_cart_add_payload(request, path_product_id=product_id)
        print('--- DEBUG: Пришли данные в корзину:', data)

        product_id_raw = data.get('product_id')
        article = data.get('article') or data.get('sku')
        supplier = data.get('supplier')
        quantity = int(data.get('quantity', 1))

        print(
            f'--- DEBUG: Ищем товар с ID: {product_id_raw} '
            f'(тип: {type(product_id_raw).__name__})'
        )
        print(f'--- DEBUG: Артикул из запроса: {article!r}, supplier: {supplier!r}')
        print(f'--- DEBUG: Всего товаров в БД Render: {Product.objects.count()}')

        if product_id_raw is not None and str(product_id_raw).strip() != '':
            try:
                pk = int(product_id_raw)
                exists = Product.objects.filter(pk=pk).exists()
                print(f'--- DEBUG: Product.objects.filter(pk={pk}).exists() = {exists}')
            except (TypeError, ValueError) as exc:
                print(f'--- DEBUG: Не удалось привести product_id к int: {exc}')

        if article:
            article_str = str(article).strip()
            matches = Product.objects.filter(article=article_str).count()
            print(
                f'--- DEBUG: Товаров с article={article_str!r}: {matches}'
            )

        cart_manager = CartManager(request)

        if product_id_raw or article:
            product, matched_by = _find_local_product(
                product_id=product_id_raw,
                article=article,
                supplier=supplier,
            )
            print(
                f'--- DEBUG: Результат поиска: product={getattr(product, "pk", None)}, '
                f'matched_by={matched_by}'
            )
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

            try:
                cart_manager.add(
                    product_id=product.id,
                    quantity=quantity,
                    mode=data.get('mode'),
                )
            except CartSellerConflictError as exc:
                return _seller_conflict_response(exc.seller_name)
            except CartModeConflictError as exc:
                return _mode_conflict_response(str(exc))
            except ValueError as exc:
                return JsonResponse(
                    {
                        'success': False,
                        'ok': False,
                        'error': str(exc),
                        'message': str(exc),
                    },
                    status=400,
                )
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
            try:
                cart_manager.add(product_id=product.id, quantity=quantity)
            except CartSellerConflictError as exc:
                return _seller_conflict_response(exc.seller_name)
            except CartModeConflictError as exc:
                return _mode_conflict_response(str(exc))
            except ValueError as exc:
                return JsonResponse(
                    {
                        'success': False,
                        'ok': False,
                        'error': str(exc),
                        'message': str(exc),
                    },
                    status=400,
                )

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
    _prune_unavailable_cart_items(request, cart)
    items = cart.get_items()
    for item in items:
        item['stock'] = public_stock_status(item.get('product'))
    wholesale_status = cart.wholesale_status()
    continue_shopping_url = reverse('catalog_list')
    seller = wholesale_status.get('seller')
    if cart.is_wholesale() and seller and seller.slug:
        continue_shopping_url = reverse(
            'public_seller_wholesale',
            kwargs={'slug': seller.slug},
        )

    wholesale_cart_conditions = []
    if cart.is_wholesale():
        wholesale_cart_conditions = wholesale_cart_condition_lines(
            build_wholesale_terms_snapshot(seller)
        )

    return render(request, 'orders/cart.html', {
        'items': items,
        'cart_total': cart.get_total(),
        'cart_count': cart.get_count(),
        'warehouse_address': _warehouse_address(),
        'wholesale_status': wholesale_status,
        'is_wholesale_cart': cart.is_wholesale(),
        'continue_shopping_url': continue_shopping_url,
        'wholesale_cart_conditions': wholesale_cart_conditions,
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


@require_POST
def cart_update_quantity(request):
    body_product_id = None
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
        body_product_id = payload.get('product_id')
    except json.JSONDecodeError:
        payload = {}

    print(
        f'--- DEBUG КОРЗИНЫ: Пришел ID {request.POST.get("product_id")} '
        f'или {body_product_id}'
    )
    print(f'--- DEBUG КОРЗИНЫ: Полный payload: {payload}')

    try:
        product_id_raw = payload.get('product_id')
        article = payload.get('article') or payload.get('sku')
        supplier = payload.get('supplier')
        quantity = int(payload.get('quantity'))
    except (TypeError, ValueError):
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': 'Некорректные product_id или quantity',
            },
            status=400,
        )

    if quantity < 1:
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': 'Количество должно быть не меньше 1',
            },
            status=400,
        )

    if not product_id_raw and not article:
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': 'Укажите product_id или article',
            },
            status=400,
        )

    product, matched_by = _find_local_product(
        product_id=product_id_raw,
        article=article,
        supplier=supplier,
    )
    print(
        f'--- DEBUG КОРЗИНЫ: Найден product_id={getattr(product, "pk", None)}, '
        f'matched_by={matched_by}'
    )

    if product is None:
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': 'Товар не найден',
            },
            status=404,
        )

    cart = CartManager(request)

    request_product_id = None
    if product_id_raw is not None and str(product_id_raw).strip() != '':
        try:
            request_product_id = int(product_id_raw)
        except (TypeError, ValueError):
            request_product_id = None

    if request_product_id and request_product_id != product.id:
        cart.remove(request_product_id)

    try:
        cart.set_quantity(product.id, quantity)
    except CartSellerConflictError as exc:
        return _seller_conflict_response(exc.seller_name)
    except CartModeConflictError as exc:
        return _mode_conflict_response(str(exc))
    except ValueError as exc:
        return JsonResponse(
            {
                'success': False,
                'ok': False,
                'error': str(exc),
                'message': str(exc),
            },
            status=400,
        )

    if cart.is_wholesale():
        quote = quote_public_wholesale(product, quantity)
    else:
        seller_profile = get_request_seller_profile(request)
        quote = resolve_commercial_price(
            product,
            quantity,
            seller_profile=seller_profile,
        )
    item_total = quote.total_price if quote.can_buy else 0
    unit_price = quote.unit_price
    cart_total = cart.get_total()
    total_items = cart.get_total_items()
    wholesale_status = cart.wholesale_status()

    return JsonResponse({
        'success': True,
        'ok': True,
        'product_id': product.id,
        'quantity': quantity,
        'item_total_price': item_total,
        'item_total_price_display': _format_price_kzt(item_total or 0),
        'cart_total_price': cart_total,
        'cart_total_price_display': _format_price_kzt(cart_total),
        'total_items': total_items,
        'cart_count': total_items,
        'unit_price': unit_price,
        'unit_price_display': _format_price_kzt(unit_price or 0),
        'price_type': quote.price_type,
        'price_label': quote.label,
        'wholesale_enabled': wholesale_status['enabled'],
        'wholesale_remaining': wholesale_status['remaining'],
        'wholesale_minimum': wholesale_status['minimum'],
        'wholesale_total_qty': wholesale_status['total_qty'],
        'wholesale_can_checkout': wholesale_status['can_checkout'],
    })


@require_http_methods(['GET', 'POST'])
def checkout(request):
    cart = CartManager(request)
    removed = _prune_unavailable_cart_items(request, cart)
    items = cart.get_items()

    if not items:
        if not removed:
            messages.warning(request, 'Корзина пуста. Добавьте товары перед оформлением заказа.')
        return redirect('catalog_list')

    wholesale_status = cart.wholesale_status()
    if cart.is_wholesale() and not wholesale_status['can_checkout']:
        messages.error(request, _wholesale_minimum_message(wholesale_status))
        return redirect('orders:cart')

    pickup_options = resolve_pickup_options(items)
    pickup_available = pickup_options['pickup_available']
    effective_pickup_address = pickup_options['effective_pickup_address']
    seller_profile = pickup_options['seller_profile']
    seller_profile_id = seller_profile.pk if seller_profile else None

    if request.method == 'POST':
        form = CheckoutForm(
            request.POST,
            pickup_available=pickup_available,
            pickup_address=effective_pickup_address,
            seller_profile_id=seller_profile_id,
        )
        if form.is_valid():
            try:
                seller_snapshot = get_seller_snapshot_from_items(items)
            except CartSellerConflictError as exc:
                messages.error(
                    request,
                    (
                        f'В корзине уже есть товары продавца «{exc.seller_name}». '
                        f'Сначала оформите текущий заказ или очистите корзину.'
                    ),
                )
                return redirect('orders:cart')

            order = None
            with transaction.atomic():
                current_items = cart.get_items()
                if not current_items:
                    messages.warning(request, 'Корзина пуста. Добавьте товары перед оформлением заказа.')
                    return redirect('catalog_list')

                seller_snapshot = get_seller_snapshot_from_items(current_items)
                is_wholesale_cart = cart.is_wholesale()
                order_type = (
                    Order.ORDER_TYPE_WHOLESALE
                    if is_wholesale_cart
                    else Order.ORDER_TYPE_RETAIL
                )
                utm_snapshot = get_utm_snapshot(request)
                terms_snapshot = {}
                if is_wholesale_cart:
                    owner = resolve_wholesale_owner(current_items[0]['product'])
                    total_qty = sum(item['quantity'] for item in current_items)
                    remaining = remaining_wholesale_qty(total_qty, owner)
                    if owner is None or remaining > 0:
                        messages.error(
                            request,
                            _wholesale_minimum_message({
                                'remaining': remaining,
                                'minimum': int(
                                    getattr(owner, 'wholesale_min_order_qty', 0) or 0
                                ) if owner else 0,
                            }),
                        )
                        return redirect('orders:cart')
                    terms_snapshot = build_wholesale_terms_snapshot(owner)
                seller_profile = get_request_seller_profile(request)
                order_lines = []
                total_price = 0
                for item in current_items:
                    product = (
                        Product.objects.select_for_update()
                        .filter(pk=item['product'].pk, status='active')
                        .first()
                    )
                    if product is None:
                        messages.error(request, 'Товар больше недоступен.')
                        return redirect('orders:cart')
                    if is_wholesale_cart:
                        quote = quote_public_wholesale(product, item['quantity'])
                    else:
                        quote = resolve_commercial_price(
                            product,
                            item['quantity'],
                            seller_profile=seller_profile,
                        )
                    if not quote.can_buy or quote.unit_price is None:
                        messages.error(
                            request,
                            quote.reason or 'Не удалось рассчитать цену товара. Обновите корзину.',
                        )
                        return redirect('orders:cart')
                    order_lines.append((product, item['quantity'], quote.unit_price))
                    total_price += quote.total_price

                order = Order.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    customer_name=form.cleaned_data['customer_name'],
                    customer_phone=form.cleaned_data['customer_phone'],
                    delivery_method=form.cleaned_data['delivery_method'],
                    delivery_address=form.build_delivery_address(),
                    total_price=total_price,
                    seller_name=seller_snapshot['seller_name'],
                    seller_whatsapp=seller_snapshot['seller_whatsapp'],
                    order_type=order_type,
                    utm_source=utm_snapshot['utm_source'],
                    utm_medium=utm_snapshot['utm_medium'],
                    utm_campaign=utm_snapshot['utm_campaign'],
                    wholesale_terms_snapshot=terms_snapshot,
                    status=Order.STATUS_NEW,
                )
                OrderItem.objects.bulk_create([
                    OrderItem(
                        order=order,
                        product=product,
                        quantity=quantity,
                        price_at_purchase=unit_price,
                    )
                    for product, quantity, unit_price in order_lines
                ])

            order_id = order.pk
            access_token = order.access_token
            transaction.on_commit(lambda: send_order_admin_email(order_id))
            cart.clear()
            clear_utm(request)
            return redirect(
                'orders:order_success',
                order_id=order_id,
                access_token=access_token,
            )
    else:
        initial = {}
        if request.user.is_authenticated:
            profile = getattr(request.user, 'seller_profile', None)
            if profile:
                initial['customer_name'] = profile.name
                initial['customer_phone'] = profile.phone
        form = CheckoutForm(
            initial=initial,
            pickup_available=pickup_available,
            pickup_address=effective_pickup_address,
            seller_profile_id=seller_profile_id,
        )

    wholesale_checkout_info = {}
    if cart.is_wholesale():
        wholesale_checkout_info = wholesale_checkout_presentation(
            build_wholesale_terms_snapshot(wholesale_status.get('seller'))
        )

    return render(request, 'orders/checkout.html', {
        'form': form,
        'items': items,
        'cart_total': cart.get_total(),
        'pickup_available': pickup_available,
        'effective_pickup_address': effective_pickup_address,
        'warehouse_address': effective_pickup_address or _warehouse_address(),
        'transport_companies': TRANSPORT_COMPANIES,
        'wholesale_status': wholesale_status,
        'is_wholesale_cart': cart.is_wholesale(),
        'wholesale_checkout_info': wholesale_checkout_info,
    })


def order_success(request, order_id, access_token):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'),
        pk=order_id,
        access_token=access_token,
    )
    pickup_address = ''
    if order.delivery_method == Order.DELIVERY_PICKUP:
        pickup_address = get_order_pickup_display_address(order)
    seller_profile = None
    wholesale_storefront_url = ''
    seller_whatsapp_url = ''
    if order.is_wholesale:
        seller_profile = resolve_seller_profile_from_order(order)
        if (
            seller_profile is not None
            and seller_profile.wholesale_enabled
            and seller_profile.slug
        ):
            wholesale_storefront_url = reverse(
                'public_seller_wholesale',
                kwargs={'slug': seller_profile.slug},
            )
        wa_text = (
            f'Здравствуйте!\n'
            f'Я оформил оптовый заказ №{order.id} на ZPT.KZ.\n'
            f'Подскажите, пожалуйста, по подтверждению наличия и доставке.'
        )
        seller_whatsapp_url = build_whatsapp_url(order.seller_whatsapp, wa_text)
    wholesale_terms_display = None
    if order.is_wholesale:
        wholesale_terms_display = wholesale_success_presentation(
            order.wholesale_terms_snapshot or {}
        )
    return render(request, 'orders/order_success.html', {
        'order': order,
        'pickup_address': pickup_address,
        'warehouse_address': pickup_address,
        'seller_profile': seller_profile,
        'wholesale_storefront_url': wholesale_storefront_url,
        'seller_whatsapp_url': seller_whatsapp_url,
        'order_total_qty': order.total_quantity,
        'wholesale_terms_display': wholesale_terms_display,
    })
