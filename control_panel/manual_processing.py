"""Private owner-operated recipient selection. No outbound message is sent here."""
from copy import copy
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
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


def recipient_rows(req, params, *, seller_ids=None):
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
    qs = Seller.objects.filter(is_test_seller=False)
    if seller_ids is not None:
        qs = qs.filter(pk__in=seller_ids)
    if params.get('scope') != 'all' and req.search_scope in ('city', 'custom'):
        qs = qs.filter(territory | universal)
    one_time_sent = RequestDispatch.objects.filter(request=req, status=RequestDispatch.STATUS_SENT,
        delivery_channel=RequestDispatch.DELIVERY_MANUAL).exclude(permission_evidence='').exclude(
        permission_evidence__startswith='buyer_requests:')
    if params.get('consent') == 'granted':
        # Cheap database prefilter before loading thousands of unrelated profiles.
        historical = SellerContactConsent.objects.filter(
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP, status='granted',
        ).filter(Q(purpose=CONTACT_CONSENT_PURPOSE_BUYER_REQUESTS) | Q(
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
            consent_text_version__in=LEGACY_REQUEST_CONSENT_VERSIONS,
        )).values_list('seller_id', flat=True)
        channels = SellerChannelConnection.objects.filter(whatsapp_consent=True).values_list('seller_id', flat=True)
        qs = qs.filter(Q(pk__in=historical) | Q(pk__in=channels) | Q(pk__in=one_time_sent.values('seller_id')))
    if params.get('audit_status') in dict(Seller._meta.get_field('audit_status').choices):
        qs = qs.filter(audit_status=params['audit_status'])
    if params.get('kaspi') in dict(Seller._meta.get_field('audit_kaspi').choices):
        qs = qs.filter(audit_kaspi=params['kaspi'])
    if params.get('activity') in dict(Seller._meta.get_field('audit_activity').choices):
        qs = qs.filter(audit_activity=params['activity'])
    country = params.get('country', '').strip()
    if country:
        qs = qs.filter(Q(selected_countries__name__iexact=country) | Q(country_fk__name__iexact=country)).distinct()
    qs = qs.defer('notes').select_related('brand_fk', 'model_fk', 'country_fk', 'channel_connection').prefetch_related(
        'selected_brands', 'selected_models', 'selected_categories', 'selected_countries', 'extra_phones', 'contact_consents',
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
    sent_records = {d.seller_id: d for d in RequestDispatch.objects.filter(request=req, status=RequestDispatch.STATUS_SENT)}
    sent_ids = set(sent_records)
    from core.services.seller_matching import assess
    rows = []
    for seller in qs:
        test = test_receiver_decision(seller, req) is True
        # Narrow by the request specialization; registration state stays visible separately.
        assessment = assess(seller, req)
        if seller_ids is None and not test and assessment.status == 'excluded':
            continue
        if any(params.get(k, '').strip() for k in ('brand', 'model', 'category')) and not _legacy_selected(seller, probe):
            continue
        consent = manual_consent_status(seller)
        dispatch = sent_records.get(seller.pk)
        if not consent and dispatch and dispatch.delivery_channel == RequestDispatch.DELIVERY_MANUAL and dispatch.permission_evidence.strip() and not dispatch.permission_evidence.startswith('buyer_requests:'):
            consent = 'one_time'
        if params.get('consent') == 'granted' and consent not in ('granted', 'one_time'):
            continue
        if params.get('consent') == 'missing' and consent in ('granted', 'one_time'):
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
                     'match_status': assessment.status, 'match_reason': assessment.reason, 'match_rank': assessment.rank,
                     'outside': outside_request_territory(req, seller), 'sent': seller.pk in sent_ids})
    rows.sort(key=lambda row: (row['match_status'] != 'matched', row['match_rank'], row['consent'] != 'granted', row['seller'].name.casefold(), row['seller'].pk))
    return rows


@control_staff_required
@require_http_methods(['GET', 'POST'])
def seller_picker(request, pk, review=False):
    from control_panel.views import _base_context
    req = authorized(request, pk)
    target = reverse('control_panel:parts_request_sellers', args=[pk])
    key = f'manual_recipients:{pk}'
    selected = set(Seller.objects.filter(pk__in=request.session.get(key, []),
        is_test_seller=False).values_list('pk', flat=True))
    request.session[key] = sorted(selected)
    review_target = reverse('control_panel:parts_request_dispatch_review', args=[pk])
    if request.method == 'POST' and request.POST.get('action') == 'toggle':
        if not request.POST.get('seller_id', '').isdigit() or request.POST.get('checked') not in ('yes', 'no'):
            return JsonResponse({'ok': False, 'error': 'Проверьте выбранный магазин.'}, status=400)
        seller = get_object_or_404(Seller, pk=request.POST['seller_id'])
        if request.POST.get('checked') == 'yes':
            try:
                if seller.is_test_seller:
                    raise ValidationError('Тестовый магазин исключён из ручного выбора.')
                validate_manual_recipient(req, seller, allow_outside_territory=request.GET.get('scope') == 'all')
                if RequestDispatch.objects.filter(request=req, seller=seller, status=RequestDispatch.STATUS_SENT).exists():
                    raise ValidationError('Эта заявка уже отправлена магазину.')
            except ValidationError as exc:
                return JsonResponse({'ok': False, 'error': '; '.join(exc.messages)}, status=400)
            selected.add(seller.pk)
        else:
            selected.discard(seller.pk)
        request.session[key] = sorted(selected)
        return JsonResponse({'ok': True, 'selected_count': len(selected)})
    tab = request.GET.get('tab', 'ready')
    if tab not in ('ready', 'selected', 'sent', 'blocked', 'review'):
        tab = 'ready'
    count_params = request.GET.copy()
    count_params.pop('availability', None)
    params = count_params.copy()
    if review or tab == 'selected':
        # The basket spans pages, cities and filters; selection never enrolls a store.
        params.clear(); params['scope'] = 'all'
    rows = recipient_rows(req, params, seller_ids=selected if review or tab == 'selected' else None)
    eligible = {row['seller'].pk for row in rows if not row['reason']}
    count_rows = recipient_rows(req, count_params) if tab == 'selected' and not review else rows
    counts = {'ready': sum(not row['reason'] for row in count_rows), 'selected': len(selected),
        'sent': sum(row['sent'] for row in count_rows),
        'review': sum(row['match_status'] == 'review' and not row['sent'] for row in count_rows),
        'blocked': sum(bool(row['reason']) and row['match_status'] != 'review' and not row['sent'] for row in count_rows)}
    if review or tab == 'selected':
        visible_rows = rows
    elif tab == 'sent':
        visible_rows = [row for row in rows if row['sent']]
    elif tab == 'review':
        visible_rows = [row for row in rows if row['match_status'] == 'review' and not row['sent']]
    elif tab == 'blocked':
        visible_rows = [row for row in rows if row['reason'] and row['match_status'] != 'review' and not row['sent']]
    else:
        visible_rows = [row for row in rows if not row['reason']]
    page = Paginator(visible_rows, 50).get_page(request.GET.get('page'))
    if request.method == 'POST':
        action = request.POST.get('action')
        preparation_failed = False
        if action in ('select', 'select_all', 'prepare') and not review:
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
                    allow_outside = request.POST.get('allow_outside') == 'yes' if review else request.GET.get('scope') == 'all'
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
        if action in ('prepare', 'confirm_sent'):
            return redirect(review_target + anchor)
        return redirect(request.get_full_path() + anchor)
    for row in page:
        row['selected'] = row['seller'].pk in selected
    context = _base_context(active_nav='parts', title=f'Отправка заявки №{pk}' if review else f'Продавцы для заявки №{pk}', breadcrumbs=[
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
    tabs = []
    for name, label in [('ready', 'Можно отправить'), ('selected', 'Выбрано'), ('sent', 'Уже отправлено'), ('review', 'Требует проверки'), ('blocked', 'Недоступны')]:
        tab_query = query.copy(); tab_query['tab'] = name
        tabs.append({'name': name, 'label': label, 'url': '?' + tab_query.urlencode(), 'count': counts[name]})
    review_query = 'scope=all' if request.GET.get('scope') == 'all' else ''
    context.update(review=review, active_tab=tab, tabs=tabs, review_target=review_target,
                   picker_target=target, review_query=review_query, counts=counts,
                   outside_selected=any(row['outside'] for row in rows) if review else False)
    context.update(req=req, page=page, filters=request.GET, query=query.urlencode(), total=len(rows),
                   ready=len(eligible), selected_count=len(selected),
                   deliveries=deliveries)
    return render(request, 'control_panel/parts_dispatch_review.html' if review else 'control_panel/parts_sellers.html', context)


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


def seller_dispatch_review(request, pk):
    return seller_picker(request, pk, review=True)
