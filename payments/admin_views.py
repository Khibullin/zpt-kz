from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from orders.models import Order

from .exceptions import PaymentStartBlocked
from .models import PaymentAttempt
from .services import snapshot_amount, start_test_payment


def _active_superuser(request):
    user = getattr(request, 'user', None)
    return bool(
        user is not None
        and user.is_authenticated
        and user.is_active
        and user.is_superuser
    )


@require_http_methods(['GET', 'POST'])
@staff_member_required(login_url='/admin/login/')
def robokassa_test_start_view(request, order_id):
    if not _active_superuser(request):
        return HttpResponseForbidden('Тестовую оплату может запускать только superuser.')
    order = get_object_or_404(Order, pk=order_id)
    error = ''
    if request.method == 'POST':
        force_new = request.POST.get('force_new') == '1'
        try:
            payload = start_test_payment(
                request.user,
                order.pk,
                force_new=force_new,
            )
        except PaymentStartBlocked as exc:
            error = str(exc)
        else:
            return render(request, 'payments/robokassa_redirect.html', {
                'action': payload['action'],
                'fields': payload['fields'],
                'reused': payload['reused'],
                'inv_id': payload['attempt'].inv_id,
                'order_id': order.pk,
                'amount': payload['out_sum'],
            })
    try:
        amount_display = snapshot_amount(order)
    except PaymentStartBlocked:
        amount_display = order.total_price
    latest = (
        PaymentAttempt.objects.filter(order=order)
        .order_by('-inv_id')
        .first()
    )
    context = {
        **admin.site.each_context(request),
        'title': f'Тестовая оплата Robokassa, заказ №{order.pk}',
        'order': order,
        'amount_display': amount_display,
        'error': error,
        'latest': latest,
        'start_url': request.path,
        'order_admin_url': reverse('admin:orders_order_change', args=[order.pk]),
        'opts': Order._meta,
        'has_view_permission': True,
    }
    return render(request, 'admin/payments/start_test.html', context)
