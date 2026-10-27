"""Owner-only product questions, isolated from orders and request dispatch."""
import logging
import uuid
from datetime import timedelta

from django import forms
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction, close_old_connections, connections
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.phone_utils import normalize_kz_phone
from .models import Product, ProductPriceInquiry

logger = logging.getLogger(__name__)
OWNER_EMAIL = 'rkhaibullin@gmail.com'
SALT = 'catalog.product-price-inquiry.v1'
CONSENT_VERSION = 'product-price-inquiry-20261027'


def can_request_product_price(product):
    name = ''.join(c for c in (product.seller_name or '').casefold() if c.isalnum())
    profile = product.seller_profile if product.seller_profile_id else None
    return (product.status == 'active' and product.price_on_request
            and name != 'agparts' and (not profile or profile.slug != 'ag-parts'))


class PriceInquiryForm(forms.Form):
    name = forms.CharField(label='Ваше имя', max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Как к вам обращаться', 'autocomplete': 'name', 'autofocus': True}))
    phone = forms.CharField(label='Телефон', max_length=30,
        widget=forms.TextInput(attrs={'type': 'tel', 'inputmode': 'tel',
            'placeholder': '+7 (___) ___-__-__', 'autocomplete': 'tel'}))
    quantity = forms.IntegerField(label='Количество', min_value=1, max_value=9999, initial=1)
    comment = forms.CharField(label='Комментарий', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Ваш вопрос о товаре — необязательно'}))
    consent = forms.BooleanField(label='Согласен на обработку персональных данных')
    submission = forms.CharField(widget=forms.HiddenInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_phone(self):
        phone = normalize_kz_phone(self.cleaned_data['phone'])
        if not phone:
            raise forms.ValidationError('Укажите телефон: +7 и 10 цифр номера.')
        return '+' + phone


@require_http_methods(['GET', 'POST'])
def product_price_inquiry(request, product_id):
    product = get_object_or_404(Product.objects.select_related('seller_profile'), pk=product_id)
    if not can_request_product_price(product):
        raise Http404
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not request.session.session_key:
        request.session.create()
    if request.method == 'POST':
        form = PriceInquiryForm(request.POST, auto_id='price_%s')
        if form.is_valid():
            try:
                payload = signing.loads(form.cleaned_data['submission'], salt=SALT, max_age=3600)
                if payload['product'] != product.pk or payload['session'] != request.session.session_key:
                    raise signing.BadSignature
                submission_id = uuid.UUID(payload['id'])
            except (signing.BadSignature, ValueError, KeyError, TypeError):
                form.add_error(None, 'Форма устарела. Закройте её и откройте снова.')
            else:
                existing = ProductPriceInquiry.objects.filter(submission_id=submission_id).first()
                if existing:
                    return _success(request, existing, ajax)
                if form.cleaned_data['website']:
                    form.add_error(None, 'Не удалось отправить запрос. Обновите страницу.')
                elif ProductPriceInquiry.objects.filter(session_key=request.session.session_key,
                        created_at__gte=timezone.now()-timedelta(minutes=10)).count() >= 5:
                    form.add_error(None, 'Вы уже отправили несколько запросов. Повторите через 10 минут.')
                else:
                    from .templatetags.product_extras import public_product_url
                    data = form.cleaned_data
                    with transaction.atomic():
                        inquiry, created = ProductPriceInquiry.objects.get_or_create(
                            submission_id=submission_id, defaults={
                                'product': product, 'product_title': product.title,
                                'article': product.article, 'seller_name': product.seller_name,
                                'product_url': request.build_absolute_uri(public_product_url(product)),
                                'name': data['name'], 'phone': data['phone'],
                                'quantity': data['quantity'], 'comment': data['comment'],
                                'session_key': request.session.session_key,
                                'consent_version': CONSENT_VERSION,
                            })
                        if created:
                            transaction.on_commit(lambda: notify_price_inquiry(inquiry.pk))
                    return _success(request, inquiry, ajax)
        if ajax:
            return JsonResponse({'success': False, 'errors': form.errors.get_json_data()}, status=400)
    else:
        token = signing.dumps({'id': str(uuid.uuid4()), 'product': product.pk,
            'session': request.session.session_key}, salt=SALT)
        form = PriceInquiryForm(initial={'submission': token}, auto_id='price_%s')
    context = {'inquiry_product': product, 'inquiry_form': form}
    response = render(request, 'catalog/includes/price_inquiry_form.html' if ajax
                      else 'catalog/price_inquiry_page.html', context)
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def _success(request, inquiry, ajax):
    message = 'Запрос принят. Мы свяжемся с вами по указанному телефону.'
    if ajax:
        return JsonResponse({'success': True, 'number': inquiry.pk, 'message': message})
    # Session-bound confirmation; no customer data in public URLs.
    return render(request, 'catalog/price_inquiry_page.html', {'inquiry_success': message})


def notify_price_inquiry(inquiry_id):
    # Use the web process's existing transactional SMTP credentials.
    # The dispatch worker is intentionally not used for these owner-only messages.
    if not getattr(settings, 'REQUEST_OWNER_EMAIL_ASYNC', True):
        return process_price_inquiry_emails(inquiry_id=inquiry_id)
    try:
        from core.services.operations_email_notifications import _get_owner_email_executor
        _get_owner_email_executor().submit(_price_inquiry_email_job, inquiry_id)
    except Exception as exc:
        logger.warning('Price inquiry mail enqueue failed: id=%s type=%s', inquiry_id, type(exc).__name__)


def _price_inquiry_email_job(inquiry_id):
    close_old_connections()
    try:
        return process_price_inquiry_emails(inquiry_id=inquiry_id)
    finally:
        connections.close_all()


def process_price_inquiry_emails(limit=10, *, inquiry_id=None):
    """Claim each queued message once. Uncertain SMTP failures require staff review."""
    queued = ProductPriceInquiry.objects.filter(mail_status='pending')
    if inquiry_id is not None:
        queued = queued.filter(pk=inquiry_id)
    ids = list(queued.order_by('created_at').values_list('pk', flat=True)[:limit])
    sent = 0
    for pk in ids:
        if not ProductPriceInquiry.objects.filter(pk=pk, mail_status='pending').update(mail_status='sending'):
            continue
        inquiry = ProductPriceInquiry.objects.get(pk=pk)
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://zpt.kz').rstrip('/')
        text = '\n'.join([
            f'Запрос цены №{inquiry.pk} с сайта ZPT.KZ',
            f'Товар: {inquiry.product_title}', f'Артикул: {inquiry.article or "—"}',
            f'Продавец: {inquiry.seller_name or "—"}', f'Карточка: {inquiry.product_url}',
            f'Количество: {inquiry.quantity}', '', f'Покупатель: {inquiry.name}',
            f'Телефон: {inquiry.phone}', f'Комментарий: {inquiry.comment or "—"}', '',
            'Открыть в контрольной панели:', base + reverse('admin:catalog_productpriceinquiry_change', args=[pk]),
            '', 'Дальнейшая обработка — вручную. Продавцу ничего не отправлено.',
        ])
        try:
            if not send_mail(subject=f'ZPT.KZ — запрос цены №{pk}: {" ".join(inquiry.product_title.split())}',
                    message=text, from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[OWNER_EMAIL], fail_silently=False):
                raise RuntimeError('Email backend did not accept message')
        except Exception as exc:
            ProductPriceInquiry.objects.filter(pk=pk, mail_status='sending').update(mail_status='failed')
            logger.warning('Price inquiry mail failed: id=%s type=%s smtp_code=%s password_configured=%s',
                pk, type(exc).__name__, getattr(exc, 'smtp_code', 'n/a'), bool(settings.EMAIL_HOST_PASSWORD))
        else:
            ProductPriceInquiry.objects.filter(pk=pk, mail_status='sending').update(
                mail_status='sent', mail_sent_at=timezone.now())
            sent += 1
    return sent
