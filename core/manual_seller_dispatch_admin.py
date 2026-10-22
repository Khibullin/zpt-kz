"""Private Django admin UI for per-seller, consent-aware manual WhatsApp sends."""
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Request, RequestDispatch, Seller, SellerRequestAccess
from core.services.manual_seller_dispatch import (
    confirm_manual_dispatch_sent,
    manual_whatsapp_message,
    manual_whatsapp_url,
    prepare_manual_dispatch,
    validate_manual_recipient,
)
from core.services.seller_buyer_request_consent import request_consent_status
from core.services.seller_request_access import build_seller_request_access_url


def _admin_url(req):
    return reverse('admin:core_request_manual_dispatch', args=(req.pk,))


def manual_dispatch_admin_view(model_admin, http_request, request_id):
    req = get_object_or_404(Request, pk=request_id)
    if not model_admin.has_change_permission(http_request, req):
        raise PermissionDenied

    if http_request.method == 'POST':
        action = http_request.POST.get('action', '')
        try:
            if action == 'prepare':
                seller = get_object_or_404(
                    Seller,
                    pk=http_request.POST.get('seller_id') or 0,
                )
                if (
                    request_consent_status(seller) != 'granted'
                    and http_request.POST.get('one_time_permission') != 'yes'
                ):
                    raise ValidationError(
                        'Разовое согласие на получение этой заявки нужно '
                        'сначала получить вне WhatsApp и подтвердить в форме.'
                    )
                prepare_manual_dispatch(
                    req,
                    seller,
                    permission_evidence=http_request.POST.get('permission_evidence', ''),
                )
                messages.success(
                    http_request,
                    'Персональная заявка подготовлена. '
                    'Откройте WhatsApp и подтвердите отправку только после её выполнения.',
                )
            elif action == 'confirm_sent':
                dispatch = get_object_or_404(
                    RequestDispatch,
                    pk=http_request.POST.get('dispatch_id') or 0,
                    request=req,
                    delivery_channel=RequestDispatch.DELIVERY_MANUAL,
                )
                if http_request.POST.get('did_send') != 'yes':
                    raise ValidationError('Подтвердите фактическую ручную отправку.')
                confirm_manual_dispatch_sent(dispatch)
                messages.success(
                    http_request, 'Отправка учтена в истории заявки и кабинете продавца.'
                )
            else:
                raise ValidationError('Неизвестное действие.')
        except ValidationError as exc:
            messages.error(http_request, '; '.join(exc.messages))
        return redirect(_admin_url(req))

    candidate_qs = Seller.objects.filter(
        city=req.city,
        transport_type=req.transport_type,
        is_active=True,
        is_paused=False,
        catalog_only=False,
    )
    if req.source == req.SOURCE_HOME_SHORT:
        if req.is_dismantler_request:
            candidate_qs = candidate_qs.filter(is_dismantler=True)
        else:
            candidate_qs = candidate_qs.filter(sells_new_parts=True)
    if req.brand:
        candidate_qs = candidate_qs.filter(
            Q(name__icontains=req.brand)
            | Q(notes__icontains=req.brand)
            | Q(selected_brands__name__iexact=req.brand)
        ).distinct()
    candidates = []
    for seller in candidate_qs.order_by('name', 'id')[:80]:
        try:
            validate_manual_recipient(req, seller)
        except ValidationError:
            continue
        candidates.append({
            'seller': seller,
            'consent': request_consent_status(seller) or 'не подтверждено',
        })
        if len(candidates) == 20:
            break

    rows = []
    deliveries = (
        RequestDispatch.objects.filter(
            request=req, delivery_channel=RequestDispatch.DELIVERY_MANUAL
        )
        .select_related('seller')
        .order_by('created_at', 'id')
    )
    for dispatch in deliveries:
        access = (
            SellerRequestAccess.objects.filter(
                request=req,
                seller=dispatch.seller,
                expires_at__gt=timezone.now(),
            ).order_by('-created_at', '-id').first()
        )
        rows.append({
            'dispatch': dispatch,
            'url': build_seller_request_access_url(access) if access else '',
            'whatsapp_url': (
                manual_whatsapp_url(dispatch.seller, req, access)
                if access and dispatch.status == RequestDispatch.STATUS_PREPARED
                else ''
            ),
            'message': (
                manual_whatsapp_message(req, build_seller_request_access_url(access))
                if access else ''
            ),
            'subscription': request_consent_status(dispatch.seller) or 'не подтверждено',
        })

    context = dict(
        model_admin.admin_site.each_context(http_request),
        title=f'Ручная обработка заявки №{req.pk}',
        opts=Request._meta,
        request_obj=req,
        candidates=candidates,
        rows=rows,
        original=req,
        change_url=reverse('admin:core_request_change', args=(req.pk,)),
    )
    return render(http_request, 'admin/core/request/manual_dispatch.html', context)
