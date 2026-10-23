"""Approved request screens; no network sends on GET or account creation."""
from datetime import timedelta
from uuid import uuid4
from urllib.parse import urlencode

from django.contrib.auth import get_user_model, login
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.connection_views import guarded, seller_or_none, login_redirect
from core.models import (Seller, Request, Match, RequestInteraction, RequestOutcomeEvent,
    SellerAccessClaim, SellerChannelConnection, SellerChannelConsentEvent, RequestSellerReview)
from core.services import request_connections as service
from core.services.seller_identity import find_sellers_by_phone
from core.workflow_forms import ClaimPhoneForm, NewShopForm, BuyerEditForm, ShopEditForm, ReviewForm


def context_request(request):
    value = request.GET.get('request', '')
    return int(value) if value.isdigit() and Request.objects.filter(pk=value).exists() else None


def source(request):
    return request.GET.get('source') if request.GET.get('source') in ('telegram', 'whatsapp') else 'site'


def limited(request, name, count=10):
    key = 'workflow:' + name + ':' + request.META.get('REMOTE_ADDR', '')
    attempts = cache.get(key, 0)
    cache.set(key, attempts + 1, 60)
    return attempts >= count


@guarded
@require_http_methods(['GET', 'POST'])
def connect(request):
    seller = seller_or_none(request)
    req_id = context_request(request)
    if seller:
        return redirect('connection_detail', req_id=req_id) if req_id else redirect('connection_feed')
    return render(request, 'core/connections/connect.html', {'req_id': req_id, 'source': source(request)})


@guarded
@require_http_methods(['GET', 'POST'])
def claim(request):
    new = request.GET.get('new') == '1'
    form = (NewShopForm if new else ClaimPhoneForm)(request.POST or None)
    sellers = []
    if request.method == 'POST':
        if limited(request, 'claim'):
            return HttpResponse('Повторите через минуту.', status=429)
        if form.is_valid():
            sellers = find_sellers_by_phone(form.cleaned_data['phone'])
            if new and not sellers:
                # Stage the draft in the signed server-side session; staff creates the
                # real account only after checking the supplied contact. Never log in
                # the browser that merely claimed a known phone number.
                data = form.cleaned_data
                with transaction.atomic():
                    seller = Seller.objects.create(name=data['name'], whatsapp=data['phone'], city=data['city'],
                        brand=data['brand'], category=data['category'], transport_type='car',
                        catalog_only=True, sells_new_parts=None, is_dismantler=False)
                    claim_obj = SellerAccessClaim.objects.create(seller=seller, request_id=context_request(request),
                        source=source(request), comment='Новый магазин. Новые: %s; б/у: %s' % (data['new_parts'], data['used_parts']))
                request.session['seller_claim'] = str(claim_obj.pk)
                return redirect('workflow_claim_status')
            if sellers:
                request.session['claim_candidates'] = [s.pk for s in sellers]
                request.session['claim_request'] = context_request(request)
                request.session['claim_source'] = source(request)
            else:
                form.add_error(None, 'Карточка не найдена. Проверьте телефон или добавьте магазин.')
    # Only the public name and city are rendered, not the raw model/contact.
    return render(request, 'core/connections/claim.html', {'form': form, 'new': new,
        'candidates': [{'pk': s.pk, 'name': s.name, 'city': s.city} for s in sellers]})


@guarded
@require_http_methods(['GET', 'POST'])
def claim_confirm(request, seller_id):
    if seller_id not in request.session.get('claim_candidates', []):
        raise Http404
    seller = get_object_or_404(Seller, pk=seller_id)
    if request.method == 'POST':
        if limited(request, 'claim-confirm', 5):
            return HttpResponse(status=429)
        claim_obj = SellerAccessClaim.objects.create(seller=seller,
            request_id=request.session.get('claim_request'), source=request.session.get('claim_source', 'site'),
            comment=request.POST.get('comment', '')[:1000])
        request.session['seller_claim'] = str(claim_obj.pk)
        request.session.pop('claim_candidates', None)
        return redirect('workflow_claim_status')
    return render(request, 'core/connections/claim_confirm.html', {'shop_name': seller.name, 'city': seller.city})


@guarded
@require_http_methods(['GET'])
def claim_status(request):
    claim_obj = get_object_or_404(SellerAccessClaim, pk=request.session.get('seller_claim'))
    # Approval NEVER authenticates the claiming session. The one-time link must be
    # delivered by staff to the verified pre-existing contact, not to this browser.
    return render(request, 'core/connections/claim_status.html', {'claim': claim_obj})


@guarded
@require_http_methods(['GET', 'POST'])
def redeem_access(request, token):
    claim_obj = get_object_or_404(SellerAccessClaim, login_token=token, status='approved')
    if claim_obj.redeemed_at or not claim_obj.token_expires_at or claim_obj.token_expires_at <= timezone.now():
        return render(request, 'core/connections/expired.html', status=410)
    if request.method == 'POST':
        with transaction.atomic():
            claim_obj = SellerAccessClaim.objects.select_for_update().get(pk=claim_obj.pk)
            if claim_obj.redeemed_at or claim_obj.token_expires_at <= timezone.now():
                return HttpResponse(status=410)
            seller = Seller.objects.select_for_update().get(pk=claim_obj.seller_id)
            if not seller.is_active or seller.is_paused or (seller.user_id and not seller.user.is_active):
                return HttpResponseForbidden()
            if not seller.user_id:
                user = get_user_model().objects.create_user(username='seller-' + uuid4().hex, password=None)
                seller.user = user
            seller.catalog_only = False
            seller.contact_access_verified = True
            seller.save(update_fields=['user', 'catalog_only', 'contact_access_verified'])
            claim_obj.redeemed_at = timezone.now()
            claim_obj.save(update_fields=['redeemed_at'])
            login(request, seller.user, backend='django.contrib.auth.backends.ModelBackend')
            request.session.set_expiry(1209600)
        return redirect('connection_detail', req_id=claim_obj.request_id) if claim_obj.request_id else redirect('connection_feed')
    return render(request, 'core/connections/access_ready.html', {'claim': claim_obj})


def buyer_request(req_id, access_token):
    req = get_object_or_404(Request, pk=req_id, access_token=access_token)
    return req if service.buyer_access_valid(req) else None


@guarded
@require_http_methods(['GET', 'POST'])
def buyer_step(request, req_id, access_token, step):
    req = buyer_request(req_id, access_token)
    if req is None:
        return render(request, 'core/connections/expired.html', status=410)
    if step not in ('accepted', 'sellers', 'edit', 'result', 'seller', 'review', 'done', 'search', 'close', 'permissions'):
        raise Http404
    form = None
    participants = Match.objects.filter(request=req).select_related('seller')
    if step == 'sellers':
        from django.core.paginator import Paginator
        items = service.recommendations(req)
        items += service.directory_suggestions(req, [item['seller'].pk for item in items])
        page = Paginator(items, 10).get_page(request.GET.get('page'))
        for index, item in enumerate(page, start=page.start_index()):
            service.record(req, item['seller'], 'buyer', 'impression', position=index)
        return render(request, 'core/connections/buyer_sellers.html', {'req': req, 'page': page})
    if step == 'permissions':
        from core.connection_views import buyer
        return buyer(request, req_id, access_token)
    if step == 'edit':
        form = BuyerEditForm(request.POST or None, request.FILES or None, instance=req)
        if request.method == 'POST' and form.is_valid():
            with transaction.atomic():
                req = Request.objects.select_for_update().get(pk=req.pk)
                form = BuyerEditForm(request.POST, request.FILES, instance=req)
                if form.is_valid():
                    edited = form.save(commit=False)
                    # Changed buyer text is private until reviewed again.
                    edited.seller_feed_enabled = False
                    edited.public_summary = ''
                    edited.save()
                    if form.cleaned_data.get('photo'):
                        from core.models import RequestPhoto
                        RequestPhoto.objects.create(request=edited, image=form.cleaned_data['photo'])
                    RequestOutcomeEvent.objects.create(request=req, result='edited', comment='Покупатель уточнил заявку; требуется проверка')
            return redirect('workflow_buyer_step', req.pk, req.access_token, 'accepted')
    if request.method == 'POST' and step in ('result', 'search', 'close'):
        choice = request.POST.get('result') if step == 'result' else step
        if choice not in ('purchased', 'search', 'close'):
            return HttpResponse('Выберите результат.', status=400)
        if choice in ('search', 'close') and step == 'result':
            return redirect('workflow_buyer_step', req.pk, req.access_token, choice)
        result = {'purchased': 'purchased', 'search': 'search_again', 'close': 'closed'}[choice]
        with transaction.atomic():
            req = Request.objects.select_for_update().get(pk=req.pk)
            if req.purchase_result != result:
                req.purchase_result = result
                req.save(update_fields=['purchase_result'])
                # Correction invalidates seller attribution and public review, never
                # leaves a stale purchase score behind.
                RequestInteraction.objects.filter(request=req, actor='buyer', kind='purchase_confirmed').delete()
                RequestSellerReview.objects.filter(request=req).delete()
                RequestOutcomeEvent.objects.create(request=req, result=result, comment=('; '.join(request.POST.getlist('reason')) + '\n' + request.POST.get('comment', ''))[:2000])
        return redirect('workflow_buyer_step', req.pk, req.access_token, 'seller' if result == 'purchased' else 'done')
    if step in ('seller', 'review') and req.purchase_result != 'purchased':
        return redirect('workflow_buyer_step', req.pk, req.access_token, 'result')
    if step == 'seller' and request.method == 'POST':
        seller_id = request.POST.get('seller')
        seller = get_object_or_404(participants, seller_id=seller_id).seller if str(seller_id).isdigit() else None
        if seller_id not in ('other', 'skip') and seller is None:
            return HttpResponse('Выберите продавца или пропустите вопрос.', status=400)
        with transaction.atomic():
            req = Request.objects.select_for_update().get(pk=req.pk)
            if req.purchase_result != 'purchased':
                return HttpResponse(status=409)
            RequestInteraction.objects.filter(request=req, actor='buyer', kind='purchase_confirmed').delete()
            RequestSellerReview.objects.filter(request=req).delete()
            if seller:
                service.record(req, seller, 'buyer', 'purchase_confirmed')
            RequestOutcomeEvent.objects.create(request=req, seller=seller, result='attribution', comment=seller_id or '')
        return redirect('workflow_buyer_step', req.pk, req.access_token, 'review' if seller else 'done')
    if step == 'review':
        event = RequestInteraction.objects.filter(request=req, actor='buyer', kind='purchase_confirmed').first()
        if event is None:
            return redirect('workflow_buyer_step', req.pk, req.access_token, 'done')
        form = ReviewForm(request.POST or None, instance=RequestSellerReview.objects.filter(request=req).first())
        if request.method == 'POST' and form.is_valid():
            review = form.save(commit=False)
            review.request, review.seller_id, review.moderated = req, event.seller_id, False
            review.save()
            return redirect('workflow_buyer_step', req.pk, req.access_token, 'done')
    return render(request, 'core/connections/buyer_step.html', {'req': req, 'step': step,
        'form': form, 'participants': participants, 'status_label': service.result_label(req)})


@guarded
@require_http_methods(['GET', 'POST'])
def profile_edit(request):
    seller = seller_or_none(request)
    if not seller:
        return login_redirect()
    form = ShopEditForm(request.POST or None, request.FILES or None, instance=seller)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('connection_quality')
    return render(request, 'core/connections/profile_edit.html', {'form': form, 'seller': seller})


@guarded
@require_http_methods(['GET', 'POST'])
def channels(request):
    seller = seller_or_none(request)
    if not seller:
        return login_redirect()
    conn, _ = SellerChannelConnection.objects.get_or_create(seller=seller)
    if request.method == 'POST':
        with transaction.atomic():
            conn = SellerChannelConnection.objects.select_for_update().get(pk=conn.pk)
            for channel in ('telegram', 'whatsapp'):
                field = channel + '_consent'
                value = request.POST.get(channel) == 'on'
                if value != getattr(conn, field):
                    SellerChannelConsentEvent.objects.create(seller=seller, channel=channel, granted=value)
                setattr(conn, field, value)
            conn.save()
        return redirect(request.path)
    return render(request, 'core/connections/channels.html', {'conn': conn, 'seller': seller})


@guarded
@require_http_methods(['GET', 'POST'])
def telegram_connect(request):
    seller = seller_or_none(request)
    if not seller:
        return login_redirect()
    conn, _ = SellerChannelConnection.objects.get_or_create(seller=seller)
    if request.method == 'POST':
        if request.POST.get('action') == 'disconnect':
            conn.telegram_id = conn.pending_telegram_id = conn.binding_token = conn.binding_expires_at = None
            if conn.telegram_consent:
                SellerChannelConsentEvent.objects.create(seller=seller, channel='telegram', granted=False)
            conn.telegram_consent = False
        else:
            conn.binding_token = uuid4()
            conn.binding_expires_at = timezone.now() + timedelta(minutes=10)
            conn.pending_telegram_id = None
        conn.save()
        return redirect(request.path)
    link = ''
    if conn.binding_token and conn.binding_expires_at and conn.binding_expires_at > timezone.now():
        link = 'https://t.me/zpt_kz_zakaz_bot?start=bind_' + conn.binding_token.hex
    return render(request, 'core/connections/telegram_connect.html', {'conn': conn, 'bot_link': link, 'seller': seller})


@guarded
@require_http_methods(['GET'])
def buyer_shop(request, req_id, access_token, seller_id):
    req = buyer_request(req_id, access_token)
    if req is None:
        return render(request, 'core/connections/expired.html', status=410)
    item = next((x for x in service.recommendations(req) if x['seller'].pk == seller_id), None)
    if item is None:
        item = next((x for x in service.directory_suggestions(req) if x['seller'].pk == seller_id), None)
    if item is None:
        raise Http404
    from core.services.seller_card_display import build_seller_card
    item['public_card'] = build_seller_card(item['seller'])
    return render(request, 'core/connections/shop.html', {'req': req, 'item': item, 'reviews': RequestSellerReview.objects.filter(seller_id=seller_id, public_consent=True, moderated=True, request__purchase_result='purchased').order_by('-created_at')[:10]})


@guarded
@require_http_methods(['POST'])
def buyer_directory_contact(request, req_id, access_token, seller_id, channel):
    req = buyer_request(req_id, access_token)
    if req is None:
        return HttpResponse(status=410)
    if channel not in ('whatsapp', 'telegram', 'call'):
        raise Http404
    item = next((x for x in service.directory_suggestions(req) if x['seller'].pk == seller_id), None)
    target = service.directory_contact(item['seller'], req, channel) if item else ''
    if not target:
        raise Http404
    service.record(req, item['seller'], 'buyer', channel + '_click', source='public_directory')
    if channel == 'call':
        from django.http import HttpResponseRedirect
        class PhoneRedirect(HttpResponseRedirect):
            allowed_schemes = ['https', 'http', 'tel']
        return PhoneRedirect(target)
    return redirect(target)


@guarded
@require_http_methods(['GET', 'POST'])
def seller_contact_page(request, req_id):
    req = get_object_or_404(Request, pk=req_id)
    seller = seller_or_none(request)
    if not seller:
        return login_redirect(req_id)
    if req.purchase_result in ('purchased', 'closed'):
        return render(request, 'core/connections/closed.html')
    if not service.can_open_contacts(req, seller):
        return HttpResponseForbidden('Сначала подтвердите доступ и отметьте интерес к заявке.')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in ('contact_confirmed', 'contact_pending'):
            return HttpResponse(status=400)
        if action == 'contact_confirmed':
            service.record(req, seller, 'seller', action)
            return redirect('workflow_seller_result', req_id)
        RequestOutcomeEvent.objects.create(request=req, seller=seller, source='seller', result=action)
        return redirect(request.path)
    return render(request, 'core/connections/seller_contact.html', {'req': req, 'telegram': bool(service.telegram_contact(req.buyer_telegram))})


@guarded
@require_http_methods(['GET', 'POST'])
def seller_result(request, req_id):
    req = get_object_or_404(Request, pk=req_id)
    seller = seller_or_none(request)
    if not seller:
        return login_redirect(req_id)
    if not service.can_open_contacts(req, seller):
        return HttpResponseForbidden()
    if request.method == 'POST':
        action = request.POST.get('result')
        if action not in ('supply_confirmed', 'needs_details', 'cannot_fulfill'):
            return HttpResponse('Выберите результат.', status=400)
        if action != 'needs_details':
            service.record(req, seller, 'seller', action)
        RequestOutcomeEvent.objects.create(request=req, seller=seller, source='seller', result=action,
                                          comment=request.POST.get('comment', '')[:2000])
        return redirect('connection_detail', req_id)
    return render(request, 'core/connections/seller_result.html', {'req': req})


@guarded
@require_http_methods(['GET', 'POST'])
def telegram_login(request):
    from core.models import TelegramSellerLogin
    if seller_or_none(request):
        return redirect('connection_feed')
    ticket = None
    ticket_id = request.session.get('telegram_login_ticket')
    if ticket_id:
        ticket = TelegramSellerLogin.objects.filter(pk=ticket_id, expires_at__gt=timezone.now(), consumed_at__isnull=True).first()
    if request.method == 'POST':
        if limited(request, 'telegram-login', 20):
            return HttpResponse(status=429)
        if request.POST.get('action') == 'start':
            ticket = TelegramSellerLogin.objects.create(expires_at=timezone.now() + timedelta(minutes=10))
            request.session['telegram_login_ticket'] = str(ticket.pk)
        elif ticket:
            with transaction.atomic():
                ticket = TelegramSellerLogin.objects.select_for_update(of=('self',)).select_related('seller__user').get(pk=ticket.pk)
                if (ticket.seller_id and ticket.seller.user_id and ticket.seller.user.is_active
                        and ticket.seller.contact_access_verified and service.seller_can_participate(ticket.seller)
                        and ticket.expires_at > timezone.now() and not ticket.consumed_at):
                    ticket.consumed_at = timezone.now()
                    ticket.save(update_fields=['consumed_at'])
                    login(request, ticket.seller.user, backend='django.contrib.auth.backends.ModelBackend')
                    request.session.pop('telegram_login_ticket', None)
                    return redirect('connection_detail', req_id=context_request(request)) if context_request(request) else redirect('connection_feed')
        return redirect(request.get_full_path())
    return render(request, 'core/connections/telegram_login.html', {'ticket': ticket})


@guarded
@require_http_methods(['GET', 'POST'])
def buyer_channels(request, req_id, access_token):
    from core.models import RequestBuyerChannel
    req = buyer_request(req_id, access_token)
    if req is None:
        return render(request, 'core/connections/expired.html', status=410)
    conn, _ = RequestBuyerChannel.objects.get_or_create(request=req)
    if request.method == 'POST':
        if request.POST.get('action') == 'disconnect':
            conn.telegram_id = conn.binding_token = conn.binding_expires_at = conn.pending_telegram_id = None
            conn.telegram_consent = False
        else:
            conn.telegram_consent = request.POST.get('consent') == 'on'
            conn.consent_at = timezone.now()
            conn.binding_token = uuid4()
            conn.binding_expires_at = timezone.now() + timedelta(minutes=10)
        conn.save()
        return redirect(request.path)
    bot_link = ''
    if conn.binding_token and conn.binding_expires_at > timezone.now():
        bot_link = 'https://t.me/zpt_kz_zakaz_bot?start=buyer_' + conn.binding_token.hex
    return render(request, 'core/connections/buyer_channels.html', {'req': req, 'conn': conn, 'bot_link': bot_link})
