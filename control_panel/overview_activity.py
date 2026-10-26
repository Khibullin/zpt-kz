"""Read-only detail lists using the same records and periods as overview counters."""
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from control_panel.auth import control_staff_required
from control_panel.periods import PERIOD_CHOICES, DEFAULT_OVERVIEW_PERIOD, normalize_period, apply_created_range
from core.models import RequestDispatch, SellerRequestPageEvent


@control_staff_required
def overview_activity(request, kind):
    from control_panel.views import _base_context
    if kind not in ('sent', 'opened'):
        raise Http404
    period = normalize_period(request.GET.get('period'), default=DEFAULT_OVERVIEW_PERIOD,
                              allowed=tuple(key for key, _ in PERIOD_CHOICES))
    if kind == 'sent':
        title = 'Отправки уведомлений продавцам'
        qs = apply_created_range(RequestDispatch.objects.filter(status=RequestDispatch.STATUS_SENT), 'sent_at', period)
        time_field = 'sent_at'
    else:
        title = 'Открытия заявок продавцами'
        qs = apply_created_range(SellerRequestPageEvent.objects.filter(event_type='page_open'), 'created_at', period)
        time_field = 'created_at'
    page = Paginator(qs.select_related('seller', 'request').order_by('-' + time_field, '-pk'), 50).get_page(request.GET.get('page'))
    rows = [dict(seller_id=item.seller_id, seller_name=item.seller.name if item.seller_id else 'Магазин удалён',
                 city=item.seller.city if item.seller_id else '', request_id=item.request_id,
                 time=getattr(item, time_field)) for item in page]
    context = _base_context(active_nav='overview', breadcrumbs=[
        {'label': 'Обзор', 'url': 'control_panel:overview'}, {'label': title}], title=title)
    context.update(rows=rows, page=page, period=period, kind=kind,
                   overview_url=reverse('control_panel:overview') + '?period=' + period)
    return render(request, 'control_panel/overview_activity.html', context)
