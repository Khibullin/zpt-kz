"""Token-protected buyer shortlist and manual message composer."""
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.connection_views import guarded
from core.workflow_views import buyer_request
from core.models import BuyerSellerSelection, Request, RequestOutcomeEvent
from core.services import buyer_selection as selection_service
from core.services import request_connections as service


def destination(req, step='selected'):
    return reverse('buyer_selection', args=[req.pk, req.access_token, step])


@guarded
@require_http_methods(['GET', 'POST'])
def workspace(request, req_id, access_token, step):
    req = buyer_request(req_id, access_token)
    if req is None:
        return render(request, 'core/connections/expired.html', status=410)
    if step in ('selected', 'send'):
        return compose_flow(request, req, step)
    if step not in ('selected', 'message', 'confirm', 'outcome', 'history', 'help'):
        raise Http404
    selected = req.buyer_selections.select_related('seller', 'access')
    entry = None
    if step in ('message', 'confirm', 'outcome'):
        seller_id = request.GET.get('seller', '')
        if not seller_id.isdigit():
            raise Http404
        entry = get_object_or_404(selected, seller_id=seller_id)
    if request.method == 'POST':
        action = request.POST.get('action')
        if step == 'selected' and action == 'remove':
            with transaction.atomic():
                Request.objects.select_for_update().get(pk=req.pk)
                seller_id = request.POST.get('seller', '')
                if not seller_id.isdigit():
                    raise Http404
                selected.filter(seller_id=seller_id).delete()
            return redirect(destination(req))
        if step == 'help':
            if not req.outcome_events.filter(result='help_requested').exists():
                RequestOutcomeEvent.objects.create(request=req, result='help_requested',
                    comment=request.POST.get('comment', '')[:2000])
            messages.success(request, 'Заявка передана оператору для подбора продавцов.')
            return redirect(destination(req, 'history'))
        if entry and step == 'message':
            # Recheck public availability and specialization at the time of contact.
            item = next(iter(selection_service.eligible(req, '*', [entry.seller_id])), None)
            if not item:
                return HttpResponse('Контакт недоступен. Выберите другого продавца.', status=409)
            text = request.POST.get('message', '').strip()
            if not text or len(text) > 4000:
                return HttpResponse('Укажите сообщение длиной до 4000 символов.', status=400)
            if action not in ('save', 'whatsapp', 'telegram'):
                raise Http404
            entry.message = text
            entry.save(update_fields=['message'])
            if action == 'save':
                messages.success(request, 'Текст сохранён.')
                return redirect(request.get_full_path())
            if action == 'whatsapp':
                from core.phone_utils import seller_whatsapp_url
                target = service.directory_contact(entry.seller, req, 'whatsapp')
                target = seller_whatsapp_url(entry.seller, text) if target else ''
            else:
                target = service.directory_contact(entry.seller, req, 'telegram')
            if not target:
                return HttpResponse('Этот контакт недоступен.', status=409)
            service.record(req, entry.seller, 'buyer', action + '_click', source='buyer_shortlist')
            # No sent status on opening the messenger.
            return redirect(target)
        if entry and step == 'confirm':
            if action not in ('sent', 'not_sent'):
                raise Http404
            with transaction.atomic():
                entry = BuyerSellerSelection.objects.select_for_update().get(pk=entry.pk)
                if action == 'sent' and entry.sent_at is None:
                    entry.sent_at = timezone.now()
                    entry.save(update_fields=['sent_at'])
                    RequestOutcomeEvent.objects.create(request=req, seller=entry.seller,
                        result='buyer_sent', comment='Покупатель подтвердил ручную отправку')
            return redirect(destination(req))
        if entry and step == 'outcome':
            value = request.POST.get('result')
            if value not in ('answered', 'unavailable', 'offer'):
                return HttpResponse('Выберите результат.', status=400)
            entry.result, entry.comment = value, request.POST.get('comment', '')[:2000]
            entry.save(update_fields=['result', 'comment'])
            RequestOutcomeEvent.objects.create(request=req, seller=entry.seller,
                result='buyer_' + value, comment=entry.comment)
            return redirect(destination(req))
    if entry and step == 'message':
        if not entry.access or entry.access.is_expired():
            from core.services.seller_request_access import create_seller_request_access
            entry.access = create_seller_request_access(request=req, seller=entry.seller, require_binding=True)
            entry.message = ''
            entry.save(update_fields=['access', 'message'])
        text = entry.message or selection_service.default_message(req, entry)
        contacts = {channel: bool(service.directory_contact(entry.seller, req, channel))
                    for channel in ('whatsapp', 'telegram')}
    else:
        text, contacts = '', {}
    entries = list(selected)
    sent = sum(bool(item.sent_at) for item in entries)
    tab = request.GET.get('tab', 'all')
    visible = [item for item in entries if tab == 'all' or
               (tab == 'sent' and item.sent_at) or (tab == 'ready' and not item.sent_at)]
    return render(request, 'core/connections/buyer_selection.html', {
        'req': req, 'step': step, 'entry': entry, 'entries': visible,
        'total': len(entries), 'sent': sent, 'ready': len(entries) - sent,
        'text': text, 'contacts': contacts, 'tab': tab,
        'events': req.outcome_events.select_related('seller').order_by('-created_at', '-pk')[:100],
        'help_requested': req.outcome_events.filter(result='help_requested').exists(),
    })


@guarded
@require_http_methods(['POST'])
def toggle(request, req_id, access_token, seller_id):
    req = buyer_request(req_id, access_token)
    if req is None:
        return HttpResponse(status=410)
    city = request.POST.get('city', '')[:100]
    with transaction.atomic():
        Request.objects.select_for_update().get(pk=req.pk)
        entry = req.buyer_selections.filter(seller_id=seller_id).first()
        if entry:
            entry.delete()
        elif req.buyer_selections.count() >= 10:
            messages.error(request, 'Выбрано 10 из 10. Уберите одного продавца, чтобы выбрать другого.')
        else:
            item = next(iter(selection_service.eligible(req, city, [seller_id])), None)
            if not item:
                raise Http404
            BuyerSellerSelection.objects.create(request=req, seller=item['seller'])
    from urllib.parse import urlencode
    return redirect(reverse('workflow_buyer_step', args=[req.pk, req.access_token, 'sellers']) +
                    '?' + urlencode({'city': city, 'page': request.POST.get('page', '1')}))

def compose_flow(request, req, step):
    entries = req.buyer_selections.select_related('seller', 'access')
    total = entries.count()
    first = entries.first()
    vehicle = ' '.join(str(v) for v in (req.brand, req.model, req.year) if v)
    base = f'Здравствуйте! Ищу запчасть на {vehicle}.\n{req.description}\nГород: {req.city}.'
    if req.requested_quantity:
        base += f'\nКоличество: {req.requested_quantity} шт.'
    suggestion = 'Подскажите наличие, производителя, артикул, цену и условия гарантии.'
    hints = []
    if not req.requested_quantity:
        hints.append('Укажите нужное количество деталей.')
    if any(word in req.description.lower() for word in ('крыл', 'бампер', 'фар', 'двер', 'зеркал', 'шрус')):
        hints.append('Проверьте сторону и расположение детали; при наличии добавьте артикул или фото.')
    hints.append('При необходимости добавьте объём двигателя и желаемое состояние детали: новая или б/у.')
    key = f'buyer-send-skipped:{req.pk}'
    skipped = request.session.get(key, [])
    pending = entries.filter(sent_at__isnull=True).exclude(pk__in=skipped)
    entry = pending.first() if step == 'send' else None
    if entry and not entry.message:
        return redirect(destination(req))
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if step == 'selected' and action == 'prepare':
            text = request.POST.get('message', '').strip()
            if not text or len(text) > 3000:
                error = 'Введите сообщение длиной до 3000 символов.'
            elif not total:
                error = 'Сначала выберите продавцов.'
            else:
                with transaction.atomic():
                    Request.objects.select_for_update().get(pk=req.pk)
                    entries.update(message=text)
                request.session[key] = []
                return redirect(destination(req, 'send'))
        elif step == 'send' and entry:
            # Bind the action to the displayed recipient; never advance on a stale tab.
            if request.POST.get('selection') != str(entry.pk):
                return redirect(destination(req, 'send'))
            if action == 'sent':
                with transaction.atomic():
                    locked = BuyerSellerSelection.objects.select_for_update().get(pk=entry.pk)
                    if locked.sent_at is None:
                        locked.sent_at = timezone.now()
                        locked.save(update_fields=['sent_at'])
                        RequestOutcomeEvent.objects.create(request=req, seller=entry.seller,
                            result='buyer_sent', comment='Покупатель подтвердил ручную отправку')
                return redirect(destination(req, 'send'))
            if action == 'skip':
                request.session[key] = skipped + [entry.pk]
                return redirect(destination(req, 'send'))
            if action == 'whatsapp':
                item = next(iter(selection_service.eligible(req, '*', [entry.seller_id])), None)
                if not item:
                    error = 'Контакт недоступен. Пропустите продавца.'
                elif not entry.message:
                    return redirect(destination(req))
                else:
                    from core.phone_utils import seller_whatsapp_url
                    if not service.directory_contact(entry.seller, req, 'whatsapp'):
                        error = 'WhatsApp недоступен. Пропустите продавца.'
                    else:
                        text = entry.message
                        if not entry.access or entry.access.is_expired():
                            from core.services.seller_request_access import create_seller_request_access
                            entry.access = create_seller_request_access(request=req, seller=entry.seller, require_binding=True)
                            entry.save(update_fields=['access'])
                        # Keep the approved common message unchanged; invitation is personal.
                        invitation = selection_service.default_message(req, entry)
                        if '\n\nПолучайте' in invitation:
                            text += '\n\nПолучайте' + invitation.split('\n\nПолучайте', 1)[1]
                        service.record(req, entry.seller, 'buyer', 'whatsapp_click', source='buyer_shortlist')
                        return redirect(seller_whatsapp_url(entry.seller, text))
    text = request.POST.get('message', '') if error and step == 'selected' else (first.message if first and first.message else base)
    return render(request, 'core/connections/buyer_compose.html', {
        'req': req, 'step': step, 'total': total, 'text': text, 'base': base,
        'suggestion': suggestion, 'hints': hints, 'entry': entry, 'error': error,
        'sent': entries.filter(sent_at__isnull=False).count(), 'skipped_count': len(skipped),
    })
