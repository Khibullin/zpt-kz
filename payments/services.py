"""Create and confirm Robokassa KZ test payment attempts."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from orders.models import Order, OrderItem

from . import config
from .exceptions import CallbackRejected, PaymentStartBlocked
from .models import PaymentAttempt
from .params import (
    extract_callback_strings,
    extract_single,
    parse_inv_id_string,
    parse_out_sum_decimal,
)
from .signatures import sign_init, sign_result, sign_success, signatures_equal

logger = logging.getLogger(__name__)

ALLOWED_ORDER_STATUSES = frozenset({
    Order.STATUS_NEW,
    Order.STATUS_CONFIRMED,
    Order.STATUS_AWAITING_PAYMENT,
})
BLOCKED_ORDER_STATUSES = frozenset({
    Order.STATUS_PAID,
    Order.STATUS_CANCELLED,
})
AMOUNT_QUANT = Decimal('0.01')


def snapshot_amount(order):
    try:
        amount = Decimal(order.total_price)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymentStartBlocked(
            'Некорректная сумма заказа. Тест не запущен.'
        ) from exc
    if not amount.is_finite() or amount <= 0:
        raise PaymentStartBlocked(
            'Сумма заказа должна быть больше нуля. Доставка в сумму не входит.'
        )
    return amount.quantize(AMOUNT_QUANT)


def format_out_sum(amount: Decimal) -> str:
    return format(amount.quantize(AMOUNT_QUANT), 'f')


def _require_superuser(user):
    if user is None or not user.is_authenticated or not user.is_active or not user.is_superuser:
        raise PaymentStartBlocked('Тестовую оплату может запускать только superuser.')


def _require_start_config():
    if config.live_payments_allowed():
        raise PaymentStartBlocked('Боевые платежи на этапе 1 запрещены.')
    if not config.integration_enabled() or not config.test_start_enabled():
        raise PaymentStartBlocked(
            'Тестовая оплата выключена. Нужны ROBOKASSA_ENABLED и ROBOKASSA_TEST_ENABLED.'
        )
    if config.hash_algo_test() != config.HASH_ALGO_SHA256:
        raise PaymentStartBlocked(
            'Этап 1 принимает только SHA256. Выровняйте алгоритм в кабинете и ENV.'
        )
    if not config.test_password_1() or not config.test_password_2():
        raise PaymentStartBlocked(
            'Не заданы тестовые пароли №1 и №2. Укажите их в окружении сервера.'
        )
    seller_id = config.parse_own_seller_profile_id()
    if seller_id is None:
        raise PaymentStartBlocked(
            'ROBOKASSA_OWN_SELLER_PROFILE_ID пуст или некорректен. Старт заблокирован.'
        )
    seller = config.resolve_own_seller_profile()
    if seller is None:
        raise PaymentStartBlocked(
            'Продавец из ROBOKASSA_OWN_SELLER_PROFILE_ID не найден.'
        )
    return seller


def _lock_order(order_id):
    try:
        return (
            Order.objects.select_for_update()
            .prefetch_related(
                Prefetch(
                    'items',
                    queryset=OrderItem.objects.select_related('product'),
                )
            )
            .get(pk=order_id)
        )
    except Order.DoesNotExist as exc:
        raise PaymentStartBlocked('Заказ не найден.') from exc


def _assert_order_eligible(order, seller):
    if order.status in BLOCKED_ORDER_STATUSES or order.status not in ALLOWED_ORDER_STATUSES:
        raise PaymentStartBlocked(
            'Тест доступен для заказов new, confirmed и awaiting_payment.'
        )
    items = list(order.items.all())
    if not items:
        raise PaymentStartBlocked('В заказе нет позиций.')
    for item in items:
        product = item.product
        profile_id = getattr(product, 'seller_profile_id', None)
        if profile_id is None:
            raise PaymentStartBlocked(
                'У позиции нет канонического Product.seller_profile_id. Старт запрещён.'
            )
        if int(profile_id) != int(seller.pk):
            raise PaymentStartBlocked(
                'Заказ содержит чужие или смешанные товары. Нужны только товары выбранного продавца.'
            )
        if item.price_at_purchase is None or item.quantity < 1:
            raise PaymentStartBlocked('Некорректная позиция заказа.')


def _pending_attempt(order, amount, seller_id):
    return (
        PaymentAttempt.objects.select_for_update()
        .filter(
            order=order,
            status=PaymentAttempt.STATUS_CREATED,
            mode=config.MODE_TEST,
            amount=amount,
            seller_profile_id=seller_id,
            currency=config.CURRENCY_KZT,
            hash_algo=config.HASH_ALGO_SHA256,
        )
        .order_by('-inv_id')
        .first()
    )


def _build_redirect(attempt):
    out_sum = format_out_sum(attempt.amount)
    inv_id = str(attempt.inv_id)
    password = config.test_password_1()
    signature = sign_init(
        attempt.merchant_login,
        out_sum,
        inv_id,
        password,
    )
    fields = {
        'MerchantLogin': attempt.merchant_login,
        'OutSum': out_sum,
        'InvId': inv_id,
        'Description': f'Test order {attempt.order_id}',
        'SignatureValue': signature,
        'IsTest': '1',
        'Encoding': 'utf-8',
        'Culture': 'ru',
    }
    return {
        'attempt': attempt,
        'action': config.payment_url(),
        'fields': fields,
        'out_sum': out_sum,
    }


def start_test_payment(user, order_id, *, force_new=False):
    _require_superuser(user)
    seller = _require_start_config()
    with transaction.atomic():
        order = _lock_order(order_id)
        _assert_order_eligible(order, seller)
        amount = snapshot_amount(order)
        reused = False
        attempt = None
        if not force_new:
            attempt = _pending_attempt(order, amount, seller.pk)
            if attempt is not None:
                reused = True
        if attempt is None:
            attempt = PaymentAttempt.objects.create(
                order=order,
                amount=amount,
                currency=config.CURRENCY_KZT,
                mode=config.MODE_TEST,
                hash_algo=config.HASH_ALGO_SHA256,
                merchant_login=config.merchant_login(),
                seller_profile_id=seller.pk,
                created_by=user,
                status=PaymentAttempt.STATUS_CREATED,
            )
            if attempt.inv_id > config.INVID_MAX:
                raise PaymentStartBlocked('Исчерпан диапазон InvId Robokassa.')
        payload = _build_redirect(attempt)
        payload['reused'] = reused
        logger.info(
            'robokassa test start inv_id=%s order_id=%s reused=%s',
            attempt.inv_id,
            order.pk,
            reused,
        )
        return payload


def _result_password(attempt):
    if attempt.mode != config.MODE_TEST or attempt.hash_algo != config.HASH_ALGO_SHA256:
        return ''
    return config.test_password_2()


def _success_password(attempt):
    if attempt.mode != config.MODE_TEST or attempt.hash_algo != config.HASH_ALGO_SHA256:
        return ''
    return config.test_password_1()


def inspect_callback(request, *, kind):
    out_sum_raw, inv_id_raw, signature_raw, shp = extract_callback_strings(request)
    inv_id = parse_inv_id_string(inv_id_raw)
    amount = parse_out_sum_decimal(out_sum_raw)
    return {
        'out_sum_raw': out_sum_raw,
        'inv_id_raw': inv_id_raw,
        'signature_raw': signature_raw,
        'shp': shp,
        'inv_id': inv_id,
        'amount': amount,
        'kind': kind,
    }


def _verify_attempt_signature(attempt, parsed, *, kind):
    if attempt.mode != config.MODE_TEST:
        raise CallbackRejected('not_test')
    if attempt.hash_algo != config.HASH_ALGO_SHA256:
        raise CallbackRejected('bad_algo')
    if attempt.currency != config.CURRENCY_KZT:
        raise CallbackRejected('bad_currency')
    if parsed['amount'] != attempt.amount:
        raise CallbackRejected('sum_mismatch')
    if kind == 'result':
        password = _result_password(attempt)
        expected = sign_result(
            parsed['out_sum_raw'],
            parsed['inv_id_raw'],
            password,
            parsed['shp'],
        )
    else:
        password = _success_password(attempt)
        expected = sign_success(
            parsed['out_sum_raw'],
            parsed['inv_id_raw'],
            password,
            parsed['shp'],
        )
    if not password or not signatures_equal(expected, parsed['signature_raw']):
        raise CallbackRejected('bad_sign', public_message='bad sign')


def process_result(request):
    parsed = inspect_callback(request, kind='result')
    with transaction.atomic():
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(inv_id=parsed['inv_id'])
            .first()
        )
        if attempt is None:
            logger.info('robokassa result unknown inv_id=%s', parsed['inv_id'])
            raise CallbackRejected('unknown', public_message='bad sign')
        _verify_attempt_signature(attempt, parsed, kind='result')
        if attempt.status != PaymentAttempt.STATUS_CONFIRMED:
            attempt.status = PaymentAttempt.STATUS_CONFIRMED
            attempt.confirmed_at = timezone.now()
            attempt.save(update_fields=['status', 'confirmed_at'])
            logger.info(
                'robokassa result confirmed inv_id=%s order_id=%s',
                attempt.inv_id,
                attempt.order_id,
            )
        else:
            logger.info(
                'robokassa result replay inv_id=%s order_id=%s',
                attempt.inv_id,
                attempt.order_id,
            )
        return attempt


def inspect_success(request):
    parsed = inspect_callback(request, kind='success')
    attempt = PaymentAttempt.objects.filter(inv_id=parsed['inv_id']).first()
    if attempt is None:
        raise CallbackRejected('unknown', public_message='bad sign')
    _verify_attempt_signature(attempt, parsed, kind='success')
    return attempt, parsed


def inspect_fail(request):
    inv_id_raw = extract_single(request, 'InvId')
    if inv_id_raw in (None, ''):
        return None
    try:
        inv_id = parse_inv_id_string(inv_id_raw)
    except CallbackRejected:
        return None
    return PaymentAttempt.objects.filter(inv_id=inv_id).first()
