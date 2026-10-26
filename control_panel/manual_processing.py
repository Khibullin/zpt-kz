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
from core.models import (Request, Seller, RequestDispatch, SellerRequestAccess,
    SellerContactConsent, SellerChannelConnection, CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS, CONTACT_CONSENT_PURPOSE_MARKETING)
from core.phone_utils import seller_whatsapp_phone
from core.services.manual_seller_dispatch import (
    validate_manual_recipient, prepare_manual_dispatch, confirm_manual_dispatch_sent,
    manual_consent_status, manual_whatsapp_url, _legacy_selected, outside_request_territory,
    LEGACY_REQUEST_CONSENT_VERSIONS,
)
from core.services.test_receivers import universal_test_q, test_receiver_decision
from core.services.request_connections import buyer_access_valid
from django.utils import timezone


def authorized(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if not admin.site._registry[Request].has_change_permission(request, req):
        raise PermissionDenied
    return req


def territory_marker(seller):
    return f'manual_territory_override:{seller.pk}:{seller.city}'


def territory_authorized(req, seller):
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    return LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Request),
        object_id=str(req.pk), action_flag=CHANGE, change_message=territory_marker(seller)).exists()


def recipient_rows(req, params):
    # Test receivers must be OR-ed before geography, type or direction prefilters.
    universal = universal_test_q(req)
    if params.get('scope') == 'all':
        territory = Q()
    elif req.search_scope == 'city':
        territory = Q(city=req.city)
    elif req.search_scope == 'custom':
        territory = Q(city__in=[c.strip() for c in req.selected_cities.split(',') if c.strip()])
    else:
        territory = Q()
    qs = Seller.objects.all()
    if params.get('scope') != 'all' and req.search_scope in ('city', 'custom'):
        qs = qs.filter(territory | universal)
    if params.get('consent') == 'granted':
        # Cheap database prefilter before loading thousands of unrelated profiles.
        historical = SellerContactConsent.objects.filter(
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP, status='granted',
        ).filter(Q(purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS) | Q(
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            consent_text_version__in=LEGACY_REQUEST_CONSENT_VERSIONS,
        )).values_list('seller_id', flat=True)
        channels = SellerChannelConnection.objects.filter(whatsapp_consent=True).values_list('seller_id', flat=True)
        qs = qs.filter(Q(pk__in=historical) | Q(pk__in=channels))
    qs = qs.defer('notes').select_related('brand_fk', 'model_fk', 'channel_connection').prefetch_related(
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
    sent_ids = set(RequestDispatch.objects.filter(request=req, status=RequestDispatch.STATUS_SENT).values_list('seller_id', flat=True))
    rows = []
    for seller in qs:
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
            validate_manual_recipient(req, seller, allow_outside_territory=params.get('scope') == 'all')
        except ValidationError as exc:
            reason = '; '.join(exc.messages)
        if seller.pk in sent_ids:
            reason = 'Эта заявка уже отправлена магазину.'
        if params.get('availability') == 'ready' and reason:
            continue
        if params.get('availability') == 'blocked' and not reason:
            continue
        rows.append({'seller': seller, 'phone': seller_whatsapp_phone(seller),
                     'consent': consent, 'reason': reason, 'universal': test,
                     'outside': outside_request_territory(req, seller)})
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
        preparation_failed = False
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
                    if req.purchase_result in ('purchased', 'closed') or not buyer_access_valid(req):
                        raise ValidationError('Заявка закрыта или доступ отозван/истёк.')
                    if not req.contact_consent_at:
                        raise ValidationError('Нет сохранённого разрешения покупателя на контакт с продавцами.')
                    existing = RequestDispatch.objects.filter(request=req, seller=seller).first()
                    if existing and existing.status == RequestDispatch.STATUS_SENT:
                        raise ValidationError('Эта заявка уже отправлена магазину.')
                    outside = outside_request_territory(req, seller)
                    allow_outside = request.GET.get('scope') == 'all'
                    prepare_manual_dispatch(req, seller, permission_evidence=(request.POST.get('permission_evidence', '').strip()
                        or (f'Устное разрешение на заявку №{req.pk} подтверждено сотрудником {request.user.get_username()} {timezone.now().isoformat()}'
                            if request.POST.get('one_time_permission') == 'yes' else '')),
                        allow_outside_territory=allow_outside)
                    if outside and allow_outside:
                        admin.site._registry[Request].log_change(request, req, territory_marker(seller))
                    prepared += 1
                except ValidationError as exc:
                    errors.append(seller.name + ': ' + '; '.join(exc.messages))
            if prepared:
                messages.success(request, f'Готово к ручной отправке: {prepared}. Нажмите «Отправить в WhatsApp» у нужного магазина.')
            preparation_failed = bool(errors) or not selected
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
                    selected.discard(dispatch.seller_id)
                    messages.success(request, 'Фактическая ручная отправка учтена.')
                except ValidationError as exc:
                    messages.error(request, '; '.join(exc.messages))
            else:
                messages.error(request, 'Подтвердите, что сообщение действительно отправлено.')
        request.session[key] = sorted(selected)
        anchor = ('#selection-feedback' if preparation_failed else '#send') if action == 'prepare' else ''
        return redirect(request.get_full_path() + anchor)
    for row in page:
        row['selected'] = row['seller'].pk in selected
    context = _base_context(active_nav='parts', title=f'Продавцы для заявки №{pk}', breadcrumbs=[
        {'label': 'Заявки на запчасти', 'url': 'control_panel:parts_request_list'}, {'label': f'Выбор продавцов №{pk}'},
    ])
    query = request.GET.copy(); query.pop('page', None)
    selected_sellers = Seller.objects.filter(pk__in=selected).select_related('channel_connection').prefetch_related('contact_consents')
    permission_required_names = [seller.name for seller in selected_sellers if manual_consent_status(seller) != 'granted']
    deliveries = list(RequestDispatch.objects.filter(request=req, delivery_channel=RequestDispatch.DELIVERY_MANUAL)
                      .select_related('seller').order_by('created_at', 'id'))
    prepared_deliveries = [d for d in deliveries if d.status == RequestDispatch.STATUS_PREPARED]
    sent_deliveries = [d for d in deliveries if d.status == RequestDispatch.STATUS_SENT]
    context.update(permission_required_names=permission_required_names,
                   prepared_deliveries=prepared_deliveries, sent_deliveries=sent_deliveries,
                   prepared_count=len(prepared_deliveries))
    context.update(req=req, page=page, filters=request.GET, query=query.urlencode(), total=len(rows),
                   ready=len(eligible), selected_count=len(selected),
                   deliveries=deliveries)
    return render(request, 'control_panel/parts_sellers.html', context)


@control_staff_required
@require_POST
def manual_send(request, pk, dispatch_id):
    req = authorized(request, pk)
    dispatch = get_object_or_404(RequestDispatch, pk=dispatch_id, request=req,
        delivery_channel=RequestDispatch.DELIVERY_MANUAL, status=RequestDispatch.STATUS_PREPARED)
    target = reverse('control_panel:parts_request_sellers', args=[pk])
    try:
        validate_manual_recipient(req, dispatch.seller,
            allow_outside_territory=territory_authorized(req, dispatch.seller))
        if req.purchase_result in ('purchased', 'closed') or not buyer_access_valid(req):
            raise ValidationError('Заявка закрыта или доступ отозван/истёк.')
        if not req.contact_consent_at:
            raise ValidationError('Нет сохранённого разрешения покупателя на контакт с продавцами.')
        if dispatch.permission_evidence.startswith('buyer_requests:') and manual_consent_status(dispatch.seller) != 'granted':
            raise ValidationError('Сохранённое согласие отозвано. Получите новое разрешение перед отправкой.')
        access = SellerRequestAccess.objects.filter(request=req, seller=dispatch.seller,
            expires_at__gt=timezone.now()).order_by('-created_at', '-pk').first()
        if not access:
            raise ValidationError('Ссылка истекла. Выберите магазин и подготовьте заявку повторно.')
        return redirect(manual_whatsapp_url(dispatch.seller, req, access))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect(target)
