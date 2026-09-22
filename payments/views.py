from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .exceptions import CallbackRejected
from .services import inspect_fail, inspect_success, process_result

GENERIC_RETURN = (
    'Возврат с платёжной страницы. Если оплата прошла, магазин получит '
    'отдельное серверное уведомление.'
)


def _is_superuser(request):
    user = getattr(request, 'user', None)
    return bool(
        user is not None
        and getattr(user, 'is_authenticated', False)
        and getattr(user, 'is_active', False)
        and getattr(user, 'is_superuser', False)
    )


def _generic_return(request, *, title):
    return render(request, 'payments/return.html', {
        'title': title,
        'message': GENERIC_RETURN,
        'detail': None,
        'noindex': True,
    })


def _superuser_detail(attempt, *, headline, note):
    if attempt is None:
        return {
            'headline': headline,
            'note': note,
            'inv_id': None,
            'order_id': None,
            'amount': None,
            'currency': None,
            'status': None,
            'confirmed': False,
        }
    return {
        'headline': headline,
        'note': note,
        'inv_id': attempt.inv_id,
        'order_id': attempt.order_id,
        'amount': attempt.amount,
        'currency': attempt.currency,
        'status': attempt.get_status_display(),
        'confirmed': attempt.is_confirmed,
    }


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def robokassa_result(request):
    try:
        attempt = process_result(request)
    except CallbackRejected as exc:
        return HttpResponseBadRequest(
            exc.public_message,
            content_type='text/plain; charset=utf-8',
        )
    return HttpResponse(
        f'OK{attempt.inv_id}',
        content_type='text/plain; charset=utf-8',
    )


@require_GET
def robokassa_success(request):
    if not _is_superuser(request):
        return _generic_return(request, title='Возврат после оплаты')
    try:
        attempt, _parsed = inspect_success(request)
    except CallbackRejected:
        return render(request, 'payments/return.html', {
            'title': 'Возврат после оплаты',
            'message': 'Подпись Success URL не принята. Оплата этим ответом не подтверждается.',
            'detail': None,
            'noindex': True,
        })
    if attempt.is_confirmed:
        headline = 'Тестовый Result URL уже подтвердил попытку.'
        note = (
            'Заказ не переведён в «оплачен». Склад, письма и WhatsApp не запускались. '
            'Фискализация (Receipt) на этапе 1 не используется.'
        )
    else:
        headline = 'Покупатель вернулся до серверного уведомления.'
        note = (
            'Ожидаем Result URL. Success URL не подтверждает оплату и не меняет заказ. '
            'Тестируется сохранённая сумма товаров без доставки.'
        )
    return render(request, 'payments/return.html', {
        'title': 'Тестовый возврат Robokassa',
        'message': headline,
        'detail': _superuser_detail(attempt, headline=headline, note=note),
        'noindex': True,
    })


@require_GET
def robokassa_fail(request):
    if not _is_superuser(request):
        return _generic_return(request, title='Возврат без оплаты')
    attempt = inspect_fail(request)
    if attempt is None:
        headline = 'Возврат Fail URL.'
        note = 'Попытка не найдена или InvId не передан. Заказ не менялся.'
        detail = _superuser_detail(None, headline=headline, note=note)
    elif attempt.is_confirmed:
        headline = 'Fail URL не отменяет уже подтверждённую тестовую попытку.'
        note = (
            'Result URL уже зафиксировал оплату теста. Заказ по-прежнему не «оплачен».'
        )
        detail = _superuser_detail(attempt, headline=headline, note=note)
    else:
        headline = 'Покупатель вернулся с Fail URL.'
        note = (
            'Попытка не отменена, заказ не изменён. Можно запустить ту же или новую попытку.'
        )
        detail = _superuser_detail(attempt, headline=headline, note=note)
    return render(request, 'payments/return.html', {
        'title': 'Тестовый Fail URL',
        'message': headline,
        'detail': detail,
        'noindex': True,
    })
