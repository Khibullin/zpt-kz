from functools import wraps

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from core.connection_forms import BuyerPermissionsForm, OfferForm, SellerLoginForm, SellerJoinForm
from core.models import Match, Request, RequestInteraction, RequestOffer, RequestConsentEvent
from core.services import request_connections as service
from core.services.seller_identity import authenticate_unified_seller, get_logged_request_seller, SellerIdentityError, create_unified_seller_account


def guarded(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not service.enabled():
            raise Http404
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['X-Robots-Tag'] = 'noindex, nofollow'
        response['Referrer-Policy'] = 'same-origin'
        return response
    return wrapped


def seller_or_none(request):
    seller = get_logged_request_seller(request)
    return seller if (service.seller_can_participate(seller) and request.user.is_authenticated
                      and seller.user_id == request.user.pk) else None


def login_redirect(req_id=None, source='site'):
    url = reverse('workflow_connect')
    return redirect(url + (f'?request={req_id}&source={source}' if req_id else ''))


@guarded
@require_http_methods(['GET', 'POST'])
def seller_login(request):
    req_id = request.GET.get('request', '')
    req_id = int(req_id) if req_id.isdigit() else None
    source = request.GET.get('source') if request.GET.get('source') in ('telegram', 'whatsapp') else 'site'
    if seller_or_none(request):
        return redirect('connection_detail', req_id=req_id) if req_id else redirect('connection_feed')
    form = SellerLoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        # The cache key contains no phone or credential. Production needs a shared cache.
        key = 'request-login:' + request.META.get('REMOTE_ADDR', '')
        attempts = cache.get(key, 0)
        if attempts >= 10:
            return HttpResponse('Слишком много попыток. Повторите через минуту.', status=429)
        cache.set(key, attempts + 1, 60)
        try:
            authenticate_unified_seller(request, form.cleaned_data['phone'], form.cleaned_data['password'])
        except SellerIdentityError:
            form.add_error(None, 'Не удалось войти. Проверьте телефон и пароль. Для подключения импортированной карточки обратитесь в ZPT.KZ.')
        else:
            return redirect(reverse('connection_detail', args=[req_id]) + '?source=' + source) if req_id else redirect('connection_feed')
    return render(request, 'core/connections/login.html', {'form': form, 'req_id': req_id, 'source': source})


@guarded
@require_http_methods(['GET', 'POST'])
def seller_join(request):
    req_id = request.GET.get('request', '')
    req_id = int(req_id) if req_id.isdigit() else None
    source = request.GET.get('source') if request.GET.get('source') in ('telegram', 'whatsapp') else 'site'
    form = SellerJoinForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        key = 'request-join:' + request.META.get('REMOTE_ADDR', '')
        attempts = cache.get(key, 0)
        if attempts >= 5:
            return HttpResponse('Повторите через минуту.', status=429)
        cache.set(key, attempts + 1, 60)
        try:
            with transaction.atomic():
                create_unified_seller_account(name=form.cleaned_data['name'], city=form.cleaned_data['city'],
                    whatsapp=form.cleaned_data['phone'], password=form.cleaned_data['password'])
                authenticate_unified_seller(request, form.cleaned_data['phone'], form.cleaned_data['password'])
        except IntegrityError:
            form.add_error(None, 'Аккаунт уже существует. Войдите или обратитесь в ZPT.KZ.')
        except SellerIdentityError as exc:
            form.add_error(None, exc.message)
        else:
            return redirect(reverse('connection_detail', args=[req_id]) + '?source=' + source) if req_id else redirect('connection_feed')
    return render(request, 'core/connections/join.html', {'form': form})


@guarded
@require_http_methods(['GET'])
def feed(request):
    seller = seller_or_none(request)
    rows = []
    qs = Request.objects.filter(seller_feed_enabled=True).exclude(purchase_result__in=['purchased', 'closed']).order_by('-created_at')
    for req in qs.iterator():
        summary = service.safe_summary(req)
        if summary:
            rows.append({'request_id': req.pk, 'summary': summary, 'req': req, 'interested': bool(seller and req.interactions.filter(seller=seller, kind='interest').exists())})
    if request.GET.get('mine') == '1':
        rows = [x for x in rows if x['interested']]
    for key in ('city', 'brand', 'category'):
        value = request.GET.get(key, '').strip()
        if value:
            rows = [x for x in rows if getattr(x['req'], key).casefold() == value.casefold()]
    if not seller:
        rows = [x for x in rows if x['req'].public_consent_at]
    return render(request, 'core/connections/feed.html', {'page': Paginator(rows, 20).get_page(request.GET.get('page')), 'seller': seller})


@guarded
@require_http_methods(['GET', 'POST'])
def detail(request, req_id):
    req = get_object_or_404(Request, pk=req_id, seller_feed_enabled=True)
    if not service.safe_summary(req):
        raise Http404
    seller = seller_or_none(request)
    # Public links expose only an approved summary with explicit public consent.
    if not seller and not req.public_consent_at:
        return login_redirect(req.pk, 'telegram' if request.GET.get('source') == 'telegram' else 'site')
    form = None
    message = ''
    if request.method == 'POST':
        if not seller:
            return login_redirect(req.pk, request.POST.get('source') if request.POST.get('source') in ('telegram', 'whatsapp') else 'site')
        if req.purchase_result in ('purchased', 'closed'):
            return HttpResponse('Заявка закрыта.', status=409)
        action = request.POST.get('action')
        if action == 'interest':
            try:
                access = service.participate(req, seller, request.POST.get('source') if request.POST.get('source') in ('telegram', 'whatsapp') else 'site')
            except ValueError as exc:
                return HttpResponse(str(exc), status=400)
            if service.can_open_contacts(req, seller):
                return redirect('workflow_seller_contact_page', req_id=req.pk)
            message = 'Интерес сохранён. Контакты откроются после допуска продавца и разрешения покупателя.'
        elif action == 'offer':
            if not RequestInteraction.objects.filter(request=req, seller=seller, actor='seller', kind='interest').exists():
                return HttpResponseForbidden('Сначала нажмите «Есть запчасть».')
            form = OfferForm(request.POST, instance=RequestOffer.objects.filter(request=req, seller=seller).first())
            if form.is_valid():
                with transaction.atomic():
                    # Serialize competing submissions by this seller.
                    type(seller).objects.select_for_update().get(pk=seller.pk)
                    existing = RequestOffer.objects.filter(request=req, seller=seller).first()
                    offer = form.save(commit=False)
                    if existing:
                        offer.pk = existing.pk
                    offer.request, offer.seller = req, seller
                    offer.save()
                return redirect('connection_detail', req_id=req.pk)
        elif action == 'out_of_stock':
            service.record(req, seller, 'seller', 'out_of_stock', request.POST.get('source') if request.POST.get('source') in ('telegram', 'whatsapp') else 'site')
            return redirect('connection_detail', req_id=req.pk)
        elif action in ('contact_confirmed', 'supply_confirmed', 'purchase_reported', 'out_of_stock', 'cannot_fulfill'):
            if not RequestInteraction.objects.filter(request=req, seller=seller, actor='seller', kind='interest').exists():
                return HttpResponseForbidden()
            if action in ('contact_confirmed', 'supply_confirmed', 'purchase_reported') and not service.can_open_contacts(req, seller):
                return HttpResponseForbidden('Сначала получите допуск к заявке.')
            service.record(req, seller, 'seller', action)
            return redirect('connection_detail', req_id=req.pk)
        else:
            return HttpResponse('Неизвестное действие.', status=400)
    if seller:
        service.record(req, seller, 'seller', 'opened', request.GET.get('source') if request.GET.get('source') in ('telegram', 'whatsapp') else 'site')
        form = form or OfferForm(instance=RequestOffer.objects.filter(request=req, seller=seller).first())
    return render(request, 'core/connections/detail.html', {
        'req': req, 'summary': service.safe_summary(req), 'seller': seller, 'form': form,
        'can_contact': service.can_open_contacts(req, seller), 'message': message,
        'seller_actions': [('contact_confirmed', 'Общение состоялось'), ('supply_confirmed', 'Могу поставить'),
                           ('purchase_reported', 'Сообщить о продаже'), ('out_of_stock', 'Нет в наличии'),
                           ('cannot_fulfill', 'Не могу выполнить')],
        'source': request.GET.get('source') if request.GET.get('source') in ('telegram', 'whatsapp') else 'site', 'interested': bool(seller and req.interactions.filter(seller=seller, kind='interest').exists())})


@guarded
@require_http_methods(['GET', 'POST'])
def buyer(request, req_id, access_token):
    req = get_object_or_404(Request, pk=req_id, access_token=access_token)
    if not service.buyer_access_valid(req):
        return HttpResponse('Ссылка недействительна или истекла.', status=410)
    form = BuyerPermissionsForm(initial={'public_consent': bool(req.public_consent_at),
                                       'contact_consent': bool(req.contact_consent_at), 'telegram': req.buyer_telegram,
                                       'requested_quantity': req.requested_quantity, 'buyer_priority': req.buyer_priority,
                                       'buyer_budget': req.buyer_budget})
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'permissions':
            form = BuyerPermissionsForm(request.POST)
            if form.is_valid():
                with transaction.atomic():
                    req = Request.objects.select_for_update().get(pk=req.pk)
                    for purpose, field, key in [('public', 'public_consent_at', 'public_consent'), ('contact', 'contact_consent_at', 'contact_consent')]:
                        if bool(getattr(req, field)) != form.cleaned_data[key]:
                            RequestConsentEvent.objects.create(request=req, purpose=purpose, granted=form.cleaned_data[key])
                    req.public_consent_at = (req.public_consent_at or timezone.now()) if form.cleaned_data['public_consent'] else None
                    req.contact_consent_at = (req.contact_consent_at or timezone.now()) if form.cleaned_data['contact_consent'] else None
                    req.buyer_telegram = form.cleaned_data['telegram']
                    req.requested_quantity = form.cleaned_data['requested_quantity']
                    req.buyer_priority = form.cleaned_data['buyer_priority']
                    req.buyer_budget = form.cleaned_data['buyer_budget']
                    req.save(update_fields=['public_consent_at', 'contact_consent_at', 'buyer_telegram',
                                            'requested_quantity', 'buyer_priority', 'buyer_budget'])
                return redirect(request.path)
        elif action in ('contact_confirmed', 'purchase_confirmed', 'no_purchase', 'search_again'):
            seller_id = request.POST.get('seller')
            seller = get_object_or_404(Match, request=req, seller_id=seller_id).seller if str(seller_id).isdigit() else None
            if seller is None:
                raise Http404
            with transaction.atomic():
                req = Request.objects.select_for_update().get(pk=req.pk)
                if req.purchase_result == 'purchased' and action != 'purchase_confirmed':
                    return HttpResponse('Покупка уже подтверждена. Для исправления обратитесь в ZPT.KZ.', status=409)
                service.record(req, seller, 'buyer', action)
                if action != 'contact_confirmed':
                    req.purchase_result = {'purchase_confirmed': 'purchased', 'no_purchase': 'closed', 'search_again': 'search_again'}[action]
                    req.save(update_fields=['purchase_result'])
            return redirect(request.path)
        else:
            return HttpResponse('Неизвестное действие.', status=400)
    items = service.recommendations(req)
    page = Paginator(items, 10).get_page(request.GET.get('page'))
    for index, item in enumerate(page.object_list, start=page.start_index()):
        service.record(req, item['seller'], 'buyer', 'impression', position=index)
    participants = Match.objects.filter(request=req).select_related('seller')
    return render(request, 'core/connections/buyer.html', {'req': req, 'form': form, 'page': page,
        'participants': participants, 'status_label': service.result_label(req),
        'buyer_actions': [('contact_confirmed', 'Продавец ответил'), ('purchase_confirmed', 'Купил'),
                          ('no_purchase', 'Закрыть без покупки'), ('search_again', 'Продолжить поиск')]})


@guarded
@require_POST
def buyer_contact(request, req_id, access_token, seller_id, channel):
    req = get_object_or_404(Request, pk=req_id, access_token=access_token)
    if not service.buyer_access_valid(req):
        return HttpResponse(status=410)
    # Resolve server-side; no arbitrary redirects and no IDOR via seller_id.
    item = next((x for x in service.recommendations(req) if x['seller'].pk == seller_id), None)
    if item is None or channel not in ('whatsapp', 'telegram'):
        raise Http404
    target = service.seller_contact(item['seller'], req, channel)
    if not target:
        raise Http404
    with transaction.atomic():
        Match.objects.get_or_create(request=req, seller=item['seller'])
        service.record(req, item['seller'], 'buyer', channel + '_click')
    if channel == 'call':
        from django.http import HttpResponseRedirect
        class PhoneRedirect(HttpResponseRedirect):
            allowed_schemes = ['https', 'http', 'tel']
        return PhoneRedirect(target)
    return redirect(target)


@guarded
@require_http_methods(['GET', 'POST'])
def seller_quality(request):
    seller = seller_or_none(request)
    if not seller:
        return login_redirect()
    if request.method == 'POST':
        seller.direct_contacts_enabled = request.POST.get('direct_contacts') == 'on'
        seller.save(update_fields=['direct_contacts_enabled'])
        return redirect(request.path)
    events = seller.interactions
    return render(request, 'core/connections/quality.html', {'seller': seller,
        'impressions': events.filter(actor='buyer', kind='impression').count(),
        'clicks': events.filter(kind__in=['whatsapp_click', 'telegram_click']).count(),
        'purchases': events.filter(actor='buyer', kind='purchase_confirmed').count()})


@guarded
@require_POST
def seller_contact(request, req_id, channel):
    req = get_object_or_404(Request, pk=req_id)
    seller = seller_or_none(request)
    if not service.can_open_contacts(req, seller):
        return HttpResponseForbidden()
    if channel == 'telegram':
        target = service.telegram_contact(req.buyer_telegram)
    elif channel == 'whatsapp':
        from core.phone_utils import build_whatsapp_url
        target = build_whatsapp_url(req.phone, f'Здравствуйте! По заявке ZPT.KZ №{req.pk}.')
    elif channel == 'call':
        from core.services.seller_identity import normalize_seller_whatsapp
        number = normalize_seller_whatsapp(req.phone)
        target = 'tel:+' + number if number else ''
    else:
        raise Http404
    if not target:
        raise Http404
    service.record(req, seller, 'seller', ('call' if channel == 'call' else channel) + '_click')
    if channel == 'call':
        from django.http import HttpResponseRedirect
        class PhoneRedirect(HttpResponseRedirect):
            allowed_schemes = ['https', 'http', 'tel']
        return PhoneRedirect(target)
    return redirect(target)
