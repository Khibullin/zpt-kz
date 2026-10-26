"""Read-only approval preview using the production templates and fictional data."""
import re
from types import SimpleNamespace
from uuid import UUID
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe
from core.models import Request, Seller, BuyerSellerSelection
from core.workflow_forms import BuyerEditForm


@require_safe
def preview(request):
    screen = request.GET.get('screen', 'accepted')
    screens = {'accepted': 'Заявка', 'edit': 'Уточнение', 'sellers': 'Каталог',
        'selected': 'Выбранные', 'message': 'Сообщение', 'confirm': 'Отправка',
        'outcome': 'Ответ продавца', 'history': 'История', 'help': 'Помощь'}
    if screen not in screens:
        raise Http404
    req = Request(pk=0, access_token=UUID(int=0), brand='Mercedes-Benz', model='E400 4MATIC',
        year=2015, city='Алматы', transport_type='car', category='Трансмиссия',
        description='Нужны наружные передние ШРУСы: левый и правый, по 1 шт. Новые, отдельно от приводов.',
        requested_quantity=2, public_summary='ШРУСы передние наружные')
    shop = Seller(pk=0, name='Демо · магазин запчастей', city='Алматы', address='Демонстрационный адрес')
    entry = BuyerSellerSelection(pk=0, request=req, seller=shop, sent_at=timezone.now(), result='offer', comment='Есть в наличии, цену уточняем')
    item = {'seller': shop, 'selected': True, 'has_whatsapp': True, 'has_telegram': True,
        'directory_only': True, 'review_count': 0}
    context = {'req': req, 'step': screen, 'entry': entry, 'entries': [entry],
        'total': 1, 'chosen_count': 1, 'sent': 1, 'ready': 0, 'tab': 'all',
        'contacts': {'whatsapp': True, 'telegram': True}, 'city_filter': 'Алматы',
        'page': Paginator([item], 10).page(1), 'form': BuyerEditForm(instance=req),
        'events': [SimpleNamespace(created_at=timezone.now(), seller=shop,
            result='buyer_sent', comment='Покупатель подтвердил отправку')],
        'text': 'Здравствуйте! Ищу наружные передние ШРУСы на Mercedes-Benz E400 4MATIC W212, 2015 года. Левый и правый, по 1 шт. Алматы.\nПодскажите наличие, производителя, артикул, цену и гарантию.\n\nПолучайте подходящие заявки покупателей через Telegram ZPT.KZ. Подключитесь и выберите категории, марки и города: [персональная ссылка продавца].'}
    template = ('buyer_step.html' if screen in ('accepted', 'edit') else
                'buyer_sellers.html' if screen == 'sellers' else 'buyer_selection.html')
    html = render_to_string('core/connections/' + template, context, request=request)
    base = reverse('buyer_selection_preview')
    nav = '<aside class="preview-banner"><strong>Просмотр для согласования · демонстрационные данные</strong><p>Сообщения не отправляются. Выберите экран:</p><nav>'
    for key, title in screens.items():
        nav += f'<a href="{base}?screen={key}">{title}</a> '
    nav += '</nav></aside>'
    html = html.replace('<main', nav + '<main', 1)
    html = html.replace('</head>', '<style>.preview-banner{max-width:1100px;margin:20px auto;padding:18px;background:#fff0d7;border:1px solid #edcf96;border-radius:14px}.preview-banner nav{display:flex;flex-wrap:wrap;gap:14px}.preview-banner a{color:#006c76}button:disabled{opacity:.65;cursor:default}</style></head>')
    # Rewrite only known fictional request links. No real token/data enters the preview.
    prefix = f'/connections/buyer/0/{UUID(int=0)}/'
    html = re.sub(re.escape(prefix) + r'(?:step|selection)/([a-z]+)/?(?:\?[^"<>]*)?',
        lambda m: base + '?screen=' + (m.group(1) if m.group(1) in screens else 'history'), html)
    html = re.sub(re.escape(prefix) + r'[^"<>\s]*', base + '?screen=selected', html)
    # Production form controls are shown for layout review, with no submit action.
    html = re.sub(r'<button(?![^>]*type="button")', '<button disabled', html)
    html = re.sub(r' onchange="[^"]*"', '', html)
    response = HttpResponse(html)
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
