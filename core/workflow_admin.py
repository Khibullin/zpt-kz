"""Staff-only workflow screens integrated with the existing Django admin."""
from datetime import timedelta
from uuid import uuid4
from urllib.parse import urlsplit
from django import forms
from django.contrib import admin
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponseForbidden, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import path, reverse
from django.utils import timezone
from core.models import (Request, Seller, SellerAccessClaim, RequestOutcomeEvent, RequestSellerReview,
    SellerChannelConnection, SellerChannelConsentEvent, RequestSellerNotification,
    TelegramRequestPublication, TelegramPublicationAttempt)
from core.services import request_connections as service
from core.services.workflow_notifications import eligible


def log(actor, obj, message):
    LogEntry.objects.log_actions(user_id=actor.pk, queryset=type(obj).objects.filter(pk=obj.pk),
        action_flag=CHANGE, change_message=message, single_object=True)


class ClaimAdminForm(forms.ModelForm):
    class Meta:
        model = SellerAccessClaim
        fields = ['status', 'reason']
    def clean(self):
        values = super().clean()
        if values.get('status') == 'approved' and not values.get('reason', '').strip():
            self.add_error('reason', 'Укажите, как проверили представителя через контакт магазина.')
        return values


@admin.register(SellerAccessClaim)
class ClaimAdmin(admin.ModelAdmin):
    form = ClaimAdminForm
    list_display = ['seller', 'status', 'created_at', 'reviewed_by']
    list_filter = ['status']
    readonly_fields = ['seller', 'request', 'source', 'comment', 'created_at', 'reviewed_at', 'reviewed_by', 'verified_access_link', 'redeemed_at']
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
    def verified_access_link(self, obj):
        if obj.status == 'approved' and obj.login_token and not obj.redeemed_at:
            return 'https://zpt.kz' + reverse('workflow_redeem', args=[obj.login_token]) + ' — передать только по проверенному контакту магазина'
        return 'Не сформирована / использована'
    verified_access_link.short_description = 'Одноразовая ссылка для проверенного представителя'
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            current = SellerAccessClaim.objects.select_for_update().get(pk=obj.pk)
            obj.reviewed_by, obj.reviewed_at = request.user, timezone.now()
            if obj.status == 'approved' and (current.status != 'approved' or not current.token_expires_at or current.token_expires_at <= timezone.now()):
                obj.login_token = uuid4()
                obj.token_expires_at = timezone.now() + timedelta(hours=2)
            elif obj.status != 'approved':
                obj.login_token = None
            super().save_model(request, obj, form, change)


class ReviewModerationForm(forms.ModelForm):
    class Meta:
        model = RequestSellerReview
        fields = '__all__'
    def clean(self):
        values = super().clean()
        if values.get('moderated') and (not self.instance.public_consent or service.PII.search(self.instance.text)):
            raise forms.ValidationError('Публикация отзыва требует согласия и текста без контактов, VIN или ссылок.')
        return values


@admin.register(RequestSellerReview)
class ReviewAdmin(admin.ModelAdmin):
    form = ReviewModerationForm
    list_display = ['request', 'seller', 'rating', 'public_consent', 'moderated']
    readonly_fields = ['request', 'seller', 'rating', 'text', 'public_consent', 'created_at']
    def has_add_permission(self, request):
        return False


@admin.register(RequestOutcomeEvent, SellerChannelConsentEvent)
class ImmutableEventAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RequestSellerNotification)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ['request', 'seller', 'channel', 'status', 'actor', 'updated_at']
    readonly_fields = [f.name for f in RequestSellerNotification._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


class ReconcileForm(forms.Form):
    url = forms.URLField(label='Ссылка на сообщение в Telegram')
    reviewed = forms.BooleanField(label='Проверил: сообщение относится к этой заявке')
    def clean_url(self):
        parsed = urlsplit(self.cleaned_data['url'])
        bits = parsed.path.strip('/').split('/')
        if (parsed.scheme != 'https' or parsed.netloc != 't.me' or parsed.query or parsed.fragment
                or len(bits) != 2 or bits[0] != 'zpt_kz_zakaz' or not bits[1].isdigit() or int(bits[1]) <= 0):
            raise forms.ValidationError('Укажите сообщение канала zpt_kz_zakaz.')
        return int(bits[1])


def admin_view(view):
    def wrapped(request, *args, **kwargs):
        if not service.enabled():
            raise Http404
        if not request.user.is_active or not request.user.is_staff or not request.user.has_perm('core.view_request'):
            return HttpResponseForbidden()
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'same-origin'
        response['X-Robots-Tag'] = 'noindex, nofollow'
        return response
    return admin.site.admin_view(wrapped)


@admin_view
def requests_list(request):
    qs = Request.objects.order_by('-created_at')
    if request.GET.get('q', '').isdigit():
        qs = qs.filter(pk=request.GET['q'])
    elif request.GET.get('q'):
        qs = qs.filter(brand__icontains=request.GET['q'][:100])
    if request.GET.get('result') in ('purchased', 'closed', 'search_again'):
        qs = qs.filter(purchase_result=request.GET['result'])
    rows = [{'req': req, 'label': service.result_label(req)} for req in qs[:100]]
    return render(request, 'core/connections/admin_list.html', {'rows': rows})


@admin_view
def control(request, req_id):
    req = get_object_or_404(Request, pk=req_id)
    return render(request, 'core/connections/admin_control.html', {'req': req, 'label': service.result_label(req),
        'events': req.interactions.select_related('seller'), 'outcomes': req.outcome_events.select_related('seller'),
        'publication': TelegramRequestPublication.objects.filter(request=req).first()})


@admin_view
def reconcile(request, req_id):
    if not request.user.has_perm('core.change_telegramrequestpublication'):
        return HttpResponseForbidden()
    req = get_object_or_404(Request, pk=req_id)
    form = ReconcileForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            Request.objects.select_for_update().get(pk=req.pk)
            pub, _ = TelegramRequestPublication.objects.get_or_create(request=req,
                defaults={'text': service.safe_summary(req), 'created_by': request.user})
            message_id = form.cleaned_data['url']
            if pub.message_id and pub.message_id != message_id:
                form.add_error(None, 'Другой номер сообщения уже сохранён. Изменение запрещено.')
            elif TelegramRequestPublication.objects.exclude(pk=pub.pk).filter(message_id=message_id).exists():
                form.add_error(None, 'Это сообщение уже связано с другой заявкой.')
            elif pub.status == 'sending':
                form.add_error(None, 'Дождитесь завершения текущей отправки.')
            else:
                pub.message_id, pub.status = message_id, 'published'
                pub.save(update_fields=['message_id', 'status', 'updated_at'])
                TelegramPublicationAttempt.objects.create(publication=pub, actor=request.user, result='manually_reconciled')
                log(request.user, req, 'Существующая публикация сверена и привязана без отправки')
                return redirect('workflow_admin_control', req.pk)
    return render(request, 'core/connections/admin_reconcile.html', {'req': req, 'form': form})


@admin_view
def manual(request, req_id):
    if not request.user.has_perm('core.add_requestsellernotification'):
        return HttpResponseForbidden()
    req = get_object_or_404(Request, pk=req_id)
    seller_id = request.GET.get('seller') or request.POST.get('seller')
    seller = get_object_or_404(Seller, pk=seller_id) if str(seller_id).isdigit() else None
    channel = request.POST.get('channel', request.GET.get('channel', 'whatsapp'))
    if channel not in ('whatsapp', 'telegram'):
        raise Http404
    error, text, target = '', '', ''
    if seller:
        if not eligible(req, seller, channel):
            error = 'Нет согласия на этот канал или уведомления приостановлены.'
        else:
            from core.services.manual_seller_dispatch import prepare_manual_dispatch, confirm_manual_dispatch_sent
            from core.phone_utils import build_whatsapp_url
            from django.core.exceptions import ValidationError
            from core.models import RequestDispatch, SellerRequestAccess
            if req.pk == 515 and seller.pk not in (464, 466):
                error = 'Для заявки №515 разрешены только ранее согласованные получатели.'
            elif request.method == 'POST' and request.POST.get('action') == 'prepare':
                try:
                    dispatch, access = prepare_manual_dispatch(req, seller,
                        permission_evidence='Согласие продавца в настройках каналов ZPT.KZ')
                    request.session['manual_dispatch_' + str(req.pk) + '_' + str(seller.pk)] = dispatch.pk
                except ValidationError as exc:
                    error = ' '.join(exc.messages)
            dispatch_id = request.session.get('manual_dispatch_' + str(req.pk) + '_' + str(seller.pk))
            dispatch = RequestDispatch.objects.filter(pk=dispatch_id, request=req, seller=seller).first() if dispatch_id else None
            access = SellerRequestAccess.objects.filter(request=req, seller=seller, expires_at__gt=timezone.now()).order_by('-created_at').first() if dispatch else None
            if access and not error:
                text = 'Здравствуйте!\n' + service.safe_summary(req) + '\nhttps://zpt.kz' + reverse('seller_request_link', args=[access.token])
                target = build_whatsapp_url(seller.whatsapp, text) if channel == 'whatsapp' else ''
            if request.method == 'POST' and request.POST.get('action') == 'sent' and not error:
                if request.POST.get('confirmed') != 'on' or not dispatch or not access:
                    error = 'Сначала подготовьте и отправьте сообщение, затем подтвердите отправку.'
                else:
                    try:
                        confirm_manual_dispatch_sent(dispatch)
                    except ValidationError as exc:
                        error = ' '.join(exc.messages)
                    else:
                        log(request.user, req, 'Сотрудник отметил ручную отправку: продавец %s, канал %s' % (seller.pk, channel))
                        return redirect('workflow_admin_control', req.pk)
    return render(request, 'core/connections/admin_manual.html', {'req': req, 'seller': seller,
        'sellers': Seller.objects.filter(is_active=True)[:500], 'text': text, 'target': target, 'channel': channel, 'error': error})


@admin_view
def seller_activity(request, seller_id):
    if not request.user.has_perm('core.change_seller'):
        return HttpResponseForbidden()
    seller = get_object_or_404(Seller, pk=seller_id)
    error = ''
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()[:300]
        state = request.POST.get('state')
        if not reason or state not in ('active', 'suspended'):
            error = 'Укажите причину решения.'
        else:
            seller.notification_state, seller.notification_note = state, reason
            seller.save(update_fields=['notification_state', 'notification_note'])
            log(request.user, seller, 'Уведомления: %s. %s' % (state, reason))
            return redirect(request.path)
    since = timezone.now() - timedelta(days=30)
    return render(request, 'core/connections/admin_activity.html', {'seller': seller, 'error': error,
        'sent': seller.request_notifications.filter(created_at__gte=since, status__in=['sent', 'manual']).count() + seller.dispatches.filter(sent_at__gte=since).count(),
        'opened': seller.interactions.filter(kind='opened', created_at__gte=since).count(),
        'answered': seller.interactions.filter(kind__in=['interest', 'out_of_stock', 'cannot_fulfill'], created_at__gte=since).values('request_id').distinct().count()})

urlpatterns = [
    path('staff/requests/', requests_list, name='workflow_admin_list'),
    path('staff/request/<int:req_id>/', control, name='workflow_admin_control'),
    path('staff/request/<int:req_id>/reconcile/', reconcile, name='workflow_admin_reconcile'),
    path('staff/request/<int:req_id>/manual/', manual, name='workflow_admin_manual'),
    path('staff/seller/<int:seller_id>/', seller_activity, name='workflow_admin_activity'),
]


@admin_view
def publish(request, req_id):
    if not request.user.has_perm('core.change_telegramrequestpublication'):
        return HttpResponseForbidden()
    if request.method != 'POST':
        return HttpResponse(status=405)
    from core.services.telegram_requests import publish as publish_one
    pub = get_object_or_404(TelegramRequestPublication, request_id=req_id)
    try:
        publish_one(pub.pk, request.user)
    except ValueError:
        # The safe persisted status is shown, without retrying external sendMessage.
        pass
    else:
        log(request.user, pub, 'Публикация по явной команде администратора')
    return redirect('admin:core_telegramrequestpublication_change', pub.pk)

urlpatterns += [path('staff/request/<int:req_id>/publish/', publish, name='workflow_admin_publish')]

from core.models import RequestBuyerFollowup


@admin.register(RequestBuyerFollowup)
class BuyerFollowupAdmin(admin.ModelAdmin):
    list_display = ['request', 'channel', 'status', 'actor', 'updated_at']
    readonly_fields = [f.name for f in RequestBuyerFollowup._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin_view
def buyer_followup(request, req_id):
    if not request.user.has_perm('core.add_requestbuyerfollowup'):
        return HttpResponseForbidden()
    from core.models import RequestBuyerChannel
    from core.phone_utils import build_whatsapp_url
    req = get_object_or_404(Request, pk=req_id)
    if not service.buyer_access_valid(req):
        return HttpResponse('Срок доступа покупателя истёк.', status=410)
    conn = RequestBuyerChannel.objects.filter(request=req, telegram_consent=True, telegram_id__isnull=False).first()
    text = 'Удалось купить запчасть? По заявке №%s. Сообщите результат: https://zpt.kz%s' % (
        req.pk, reverse('workflow_buyer_step', args=[req.pk, req.access_token, 'result']))
    notice = RequestBuyerFollowup.objects.filter(request=req).first()
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if req.purchase_result in ('purchased', 'closed'):
            error = 'Заявка уже завершена.'
        elif action == 'telegram' and conn:
            from django.core.management import call_command
            from django.core.management.base import CommandError
            from io import StringIO
            try:
                call_command('send_buyer_result_question', request=req.pk, actor=request.user.pk, apply=True, stdout=StringIO())
            except CommandError as exc:
                error = str(exc)
        elif action == 'manual' and request.POST.get('confirmed') == 'on':
            with transaction.atomic():
                Request.objects.select_for_update().get(pk=req.pk)
                notice, _ = RequestBuyerFollowup.objects.get_or_create(request=req, defaults={'channel': 'whatsapp'})
                if notice.status not in ('prepared', 'manual'):
                    error = 'Вопрос уже отправлен или результат неизвестен.'
                else:
                    notice.status, notice.actor, notice.channel = 'manual', request.user, 'whatsapp'
                    notice.save()
                    log(request.user, req, 'Отмечена ручная отправка вопроса покупателю в WhatsApp')
        else:
            error = 'Выберите действие и подтвердите фактическую отправку.'
        if not error:
            return redirect(request.path)
    return render(request, 'core/connections/admin_buyer_followup.html', {'req':req, 'text':text, 'notice':notice,
        'target':build_whatsapp_url(req.phone, text), 'telegram_available': bool(conn), 'error':error})

urlpatterns += [path('staff/request/<int:req_id>/buyer-result/', buyer_followup, name='workflow_admin_buyer_followup')]
