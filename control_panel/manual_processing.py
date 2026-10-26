"""Private owner-operated recipient selection. No outbound message is sent here."""
from copy import copy
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from control_panel.auth import control_staff_required
from core.models import Request, Seller, RequestDispatch, SellerRequestAccess
from core.phone_utils import seller_whatsapp_phone
from core.services.manual_seller_dispatch import (
    validate_manual_recipient, prepare_manual_dispatch, confirm_manual_dispatch_sent,
    manual_consent_status, manual_whatsapp_url, _legacy_selected,
)
from core.services.test_receivers import universal_test_q, test_receiver_decision
from django.utils import timezone


def authorized(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if not admin.site._registry[Request].has_change_permission(request, req):
        raise PermissionDenied
    return req


def recipient_rows(req, params):
    # Test receivers must be OR-ed before geography, type or direction prefilters.
    universal = universal_test_q(req)
    if req.search_scope == 'city':
        territory = Q(city=req.city)
    elif req.search_scope == 'custom':
        territory = Q(city__in=[c.strip() for c in req.selected_cities.split(',') if c.strip()])
    else:
        territory = Q()
    qs = Seller.objects.filter(territory | universal).select_related('brand_fk', 'model_fk', 'channel_connection').prefetch_related(
        'selected_brands', 'selected_models', 'selected_categories', 'extra_phones', 'contact_consents',
    ).order_by('-is_test_seller', 'name', 'id')
    q = params.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(whatsapp__icontains=q) | Q(phone2__icontains=q))
    city = params.get('city', '').strip()
    if city:
        qs = qs.filter(city=city)
    probe = copy(req)
    for field in ('brand', 'model', 'category'):
        if params.get(field, '').strip():
            setattr(probe, field, params[field].strip())
    rows = []
    for seller in qs.iterator(chunk_size=200):
        test = test_receiver_decision(seller, req) is True
        # Narrow by the request specialization; registration state stays visible separately.
        if not test and (seller.transport_type != req.transport_type or not _legacy_selected(seller, req)):
            continue
        if any(params.get(k, '').strip() for k in ('brand', 'model', 'category')) and not _legacy_selected(seller, probe):
            continue
        consent = manual_consent_status(seller)
        if params.get('consent') == 'granted' and consent != 'granted':
            continue
        if params.get('consent') == 'missing' and consent == 'granted':
            continue
        reason = ''
        try:
            validate_manual_recipient(req, seller)
        except ValidationError as exc:
            reason = '; '.join(exc.messages)
        if seller.catalog_only:
            reason = 'Карточка в каталоге: владелец ещё не подключён к заявкам.'
        if params.get('availability') == 'ready' and reason:
            continue
        if params.get('availability') == 'blocked' and not reason:
            continue
        rows.append({'seller': seller, 'phone': seller_whatsapp_phone(seller),
                     'consent': consent, 'reason': reason, 'universal': test})
    return rows


@control_staff_required
@require_http_methods(['GET', 'POST'])
def seller_picker(request, pk):
    from control_panel.views import _base_context
    req = authorized(request, pk)
    target = reverse('control_panel:parts_request_sellers', args=[pk])
    key = f'manual_recipients:{pk}'
    selected = set(request.session.get(key, []))
    rows = recipient_rows(req, request.GET)
    eligible = {row['seller'].pk for row in rows if not row['reason']}
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    if request.method == 'POST':
        action = request.POST.get('action')
        if action in ('select', 'select_all', 'prepare'):
            # Only replace the visible page's selections; preserve selections from other pages.
            visible = {row['seller'].pk for row in page}
            submitted = {int(v) for v in request.POST.getlist('seller_ids') if v.isdigit()}
            selected = (selected - visible) | (submitted & eligible & visible)
        if action == 'select_all':
            selected |= eligible
        elif action == 'clear':
            selected = set()
        elif action == 'prepare':
            prepared = 0
            errors = []
            for seller in Seller.objects.filter(pk__in=selected).order_by('name', 'pk'):
                try:
                    if manual_consent_status(seller) != 'granted' and request.POST.get('one_time_permission') != 'yes':
                        raise ValidationError('Нужно согласие продавца или подтверждение разового разрешения.')
                    prepare_manual_dispatch(req, seller, permission_evidence=request.POST.get('permission_evidence', ''))
                    prepared += 1
                except ValidationError as exc:
                    errors.append(seller.name + ': ' + '; '.join(exc.messages))
            if prepared:
                messages.success(request, f'Готово к ручной отправке: {prepared}. Нажмите «Отправить в WhatsApp» у нужного магазина.')
            if errors:
                messages.error(request, 'Не подготовлены: ' + ' | '.join(errors))
            if not selected:
                messages.error(request, 'Выберите хотя бы одного продавца.')
        elif action == 'confirm_sent':
            dispatch = get_object_or_404(RequestDispatch, pk=request.POST.get('dispatch_id') or 0,
                request=req, delivery_channel=RequestDispatch.DELIVERY_MANUAL)
            if request.POST.get('did_send') == 'yes':
                try:
                    confirm_manual_dispatch_sent(dispatch)
                    messages.success(request, 'Фактическая ручная отправка учтена.')
                except ValidationError as exc:
                    messages.error(request, '; '.join(exc.messages))
            else:
                messages.error(request, 'Подтвердите, что сообщение действительно отправлено.')
        request.session[key] = sorted(selected)
        return redirect(request.get_full_path() + ('#send' if action == 'prepare' else ''))
    for row in page:
        row['selected'] = row['seller'].pk in selected
    context = _base_context(active_nav='parts', title=f'Продавцы для заявки №{pk}', breadcrumbs=[
        {'label': 'Заявки на запчасти', 'url': 'control_panel:parts_request_list'}, {'label': f'Выбор продавцов №{pk}'},
    ])
    query = request.GET.copy(); query.pop('page', None)
    context.update(req=req, page=page, filters=request.GET, query=query.urlencode(), total=len(rows),
                   ready=len(eligible), selected_count=len(selected),
                   deliveries=RequestDispatch.objects.filter(request=req, delivery_channel=RequestDispatch.DELIVERY_MANUAL)
                   .select_related('seller').order_by('created_at', 'id'))
    return render(request, 'control_panel/parts_sellers.html', context)


@control_staff_required
@require_POST
def manual_send(request, pk, dispatch_id):
    req = authorized(request, pk)
    dispatch = get_object_or_404(RequestDispatch, pk=dispatch_id, request=req,
        delivery_channel=RequestDispatch.DELIVERY_MANUAL, status=RequestDispatch.STATUS_PREPARED)
    target = reverse('control_panel:parts_request_sellers', args=[pk])
    try:
        validate_manual_recipient(req, dispatch.seller)
        access = SellerRequestAccess.objects.filter(request=req, seller=dispatch.seller,
            expires_at__gt=timezone.now()).order_by('-created_at', '-pk').first()
        if not access:
            raise ValidationError('Ссылка истекла. Выберите магазин и подготовьте заявку повторно.')
        return redirect(manual_whatsapp_url(dispatch.seller, req, access))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect(target)
