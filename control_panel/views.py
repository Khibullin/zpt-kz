from control_panel.auth import control_staff_required
from control_panel.periods import (
    DEFAULT_OVERVIEW_PERIOD,
    LIST_PERIOD_CHOICES,
    PERIOD_CHOICES,
    normalize_period,
)
from control_panel.selectors.kaspi_products import list_kaspi_products
from control_panel.selectors.overview import overview_context
from control_panel.selectors.parts_requests import (
    get_parts_request_detail,
    list_parts_requests,
)
from control_panel.selectors.sellers import (
    get_seller_detail,
    list_seller_candidates,
    list_sellers,
)
from control_panel.selectors.service_requests import (
    get_service_request_detail,
    list_service_requests,
)
from control_panel.selectors.sto import get_sto_detail, list_sto
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import SellerLead
from core.services.seller_lead_marketplace_onboarding import (
    build_marketplace_invite_whatsapp_url,
    mark_seller_lead_invited,
    mark_seller_lead_whatsapp_unavailable,
)


NAV_SECTIONS = (
    {
        'title': '',
        'items': (('overview', 'Обзор', 'control_panel:overview'),),
    },
    {
        'title': 'Клиенты и спрос',
        'items': (
            ('parts', 'Заявки на запчасти', 'control_panel:parts_request_list'),
            ('services', 'Заявки СТО', 'control_panel:service_request_list'),
        ),
    },
    {
        'title': 'Партнёры',
        'items': (
            ('sellers', 'Продавцы', 'control_panel:seller_list'),
            ('sto', 'СТО', 'control_panel:sto_list'),
        ),
    },
    {
        'title': 'Товары',
        'items': (
            ('kaspi_products', 'Товары и цены', 'control_panel:kaspi_product_list'),
        ),
    },
    {
        'title': 'Система',
        'items': (('admin', 'Техническая Admin', 'admin:index'),),
    },
)

FUTURE_MODULES = (
    'Заказы',
    'Склады',
    'Закупки',
    'Маркетинг',
    'Аналитика',
)


def _base_context(*, active_nav: str, breadcrumbs: list[dict], title: str):
    return {
        'active_nav': active_nav,
        'nav_sections': NAV_SECTIONS,
        'breadcrumbs': breadcrumbs,
        'page_title': title,
        'period_choices': PERIOD_CHOICES,
        'list_period_choices': LIST_PERIOD_CHOICES,
    }


@control_staff_required
def overview(request):
    period = normalize_period(
        request.GET.get('period'),
        default=DEFAULT_OVERVIEW_PERIOD,
        allowed=tuple(key for key, _label in PERIOD_CHOICES),
    )
    context = _base_context(
        active_nav='overview',
        breadcrumbs=[{'label': 'Обзор'}],
        title='Обзор',
    )
    context.update(overview_context(period))
    context['period'] = period
    context['future_modules'] = FUTURE_MODULES
    return render(request, 'control_panel/overview.html', context)


@control_staff_required
def parts_request_list(request):
    context = _base_context(
        active_nav='parts',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки на запчасти'},
        ],
        title='Заявки на запчасти',
    )
    context.update(list_parts_requests(request.GET))
    return render(request, 'control_panel/parts_list.html', context)


@control_staff_required
def parts_request_detail(request, pk: int):
    if request.user.has_perm('core.change_request'):
        return parts_request_process(request, pk)
    detail = get_parts_request_detail(pk)
    context = _base_context(
        active_nav='parts',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки на запчасти', 'url': 'control_panel:parts_request_list'},
            {'label': f'Заявка №{pk}'},
        ],
        title=f'Заявка №{pk}',
    )
    context.update(detail)
    return render(request, 'control_panel/parts_detail.html', context)


@control_staff_required
def service_request_list(request):
    context = _base_context(
        active_nav='services',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки СТО'},
        ],
        title='Заявки СТО',
    )
    context.update(list_service_requests(request.GET))
    return render(request, 'control_panel/services_list.html', context)


@control_staff_required
def service_request_detail(request, pk: int):
    detail = get_service_request_detail(pk)
    context = _base_context(
        active_nav='services',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Заявки СТО', 'url': 'control_panel:service_request_list'},
            {'label': f'Заявка СТО №{pk}'},
        ],
        title=f'Заявка СТО №{pk}',
    )
    context.update(detail)
    return render(request, 'control_panel/services_detail.html', context)


@control_staff_required
def seller_list(request):
    context = _base_context(
        active_nav='sellers',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Продавцы'},
        ],
        title='Продавцы',
    )
    view_mode = (request.GET.get('view') or 'sellers').strip()
    if view_mode == 'candidates':
        context.update(list_seller_candidates(request.GET))
    else:
        context.update(list_sellers(request.GET))
    return render(request, 'control_panel/sellers_list.html', context)


@control_staff_required
def seller_candidate_invite(request, pk: int):
    lead = get_object_or_404(SellerLead, pk=pk, duplicate_of__isnull=True)
    if lead.lifecycle_status != SellerLead.LIFECYCLE_READY_TO_INVITE:
        messages.warning(
            request,
            f'#{lead.pk}: приглашение доступно только на этапе «Готов к приглашению».',
        )
        return redirect(reverse('control_panel:seller_list') + '?view=candidates')
    target = build_marketplace_invite_whatsapp_url(lead)
    if not target:
        messages.warning(request, f'#{lead.pk}: корректный WhatsApp не найден.')
        return redirect(reverse('control_panel:seller_list') + '?view=candidates')
    return redirect(target)


@control_staff_required
@require_POST
def seller_candidate_whatsapp_unavailable(request, pk: int):
    lead = get_object_or_404(SellerLead, pk=pk, duplicate_of__isnull=True)
    changed = mark_seller_lead_whatsapp_unavailable(lead)
    if changed:
        messages.success(
            request,
            f'#{lead.pk}: нерабочий WhatsApp исключён, кандидат возвращён на поиск контакта.',
        )
    else:
        messages.warning(
            request,
            f'#{lead.pk}: WhatsApp уже отсутствует или этап не допускает изменение.',
        )

    next_url = (request.POST.get('next') or '').strip()
    if not next_url.startswith('/control/partners/sellers/'):
        next_url = reverse('control_panel:seller_list') + '?view=candidates'
    return redirect(next_url)


@control_staff_required
@require_POST
def seller_candidate_mark_invited(request, pk: int):
    lead = get_object_or_404(SellerLead, pk=pk, duplicate_of__isnull=True)
    changed = mark_seller_lead_invited(lead)
    if changed:
        messages.success(request, f'#{lead.pk} отмечен как приглашённый.')
    elif lead.lifecycle_status == SellerLead.LIFECYCLE_INVITED:
        messages.info(request, f'#{lead.pk} уже отмечен как приглашённый.')
    else:
        messages.warning(
            request,
            f'#{lead.pk} нельзя отметить приглашённым: проверьте этап и WhatsApp.',
        )

    next_url = (request.POST.get('next') or '').strip()
    if not next_url.startswith('/control/partners/sellers/'):
        next_url = reverse('control_panel:seller_list') + '?view=candidates'
    return redirect(next_url)


@control_staff_required
def seller_detail(request, pk: int):
    detail = get_seller_detail(pk)
    context = _base_context(
        active_nav='sellers',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Продавцы', 'url': 'control_panel:seller_list'},
            {'label': detail['seller'].name},
        ],
        title=detail['seller'].name,
    )
    context.update(detail)
    return render(request, 'control_panel/sellers_detail.html', context)


@control_staff_required
def sto_list(request):
    context = _base_context(
        active_nav='sto',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'СТО'},
        ],
        title='СТО',
    )
    context.update(list_sto(request.GET))
    return render(request, 'control_panel/sto_list.html', context)


@control_staff_required
def sto_detail(request, pk: int):
    detail = get_sto_detail(pk)
    context = _base_context(
        active_nav='sto',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'СТО', 'url': 'control_panel:sto_list'},
            {'label': detail['seller'].name},
        ],
        title=detail['seller'].name,
    )
    context.update(detail)
    return render(request, 'control_panel/sto_detail.html', context)


@control_staff_required
def kaspi_product_list(request):
    context = _base_context(
        active_nav='kaspi_products',
        breadcrumbs=[
            {'label': 'Обзор', 'url': 'control_panel:overview'},
            {'label': 'Товары и цены'},
        ],
        title='Товары и цены',
    )
    context.update(list_kaspi_products(request.GET))
    return render(request, 'control_panel/kaspi_products.html', context)


@control_staff_required
def parts_request_process(request, pk: int):
    """Owner workspace backed by the existing consent-aware manual delivery service."""
    from urllib.parse import quote
    from django.contrib import admin
    from django.core.exceptions import PermissionDenied
    from django.contrib.admin.models import LogEntry, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from core.models import Request
    from core.manual_seller_dispatch_admin import manual_dispatch_admin_view

    req = get_object_or_404(Request, pk=pk)
    model_admin = admin.site._registry[Request]
    if not model_admin.has_change_permission(request, req):
        raise PermissionDenied
    target = reverse('control_panel:parts_request_process', args=[pk])
    from control_panel.request_workspace import with_workspace_stage, STAGE_PREFIX, STAGE_LABELS
    receipt_marker = 'Ручное уведомление покупателя: заявка принята'
    receipt_logs = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Request),
        object_id=str(pk), action_flag=CHANGE, change_message=receipt_marker)
    if request.method == 'POST' and request.POST.get('action') == 'buyer_receipt_sent':
        from core.phone_utils import build_whatsapp_url
        if build_whatsapp_url(req.phone, 'receipt') and not receipt_logs.exists():
            model_admin.log_change(request, req, receipt_marker)
            messages.success(request, 'Уведомление покупателю отмечено отправленным.')
        return redirect(target)
    if request.method == 'POST' and request.POST.get('action') in ('waiting', 'resume'):
        stage = 'waiting' if request.POST['action'] == 'waiting' else 'active'
        model_admin.log_change(request, req, STAGE_PREFIX + stage)
        messages.success(request, 'Ждём уточнения покупателя.' if stage == 'waiting' else 'Заявка возвращена в работу.')
        return redirect(target)
    if request.method == 'POST' and request.POST.get('action') == 'buyer_question':
        from core.phone_utils import build_whatsapp_url
        question = request.POST.get('question', '').strip()
        if not question or len(question) > 2000:
            messages.error(request, 'Введите вопрос покупателю до 2000 символов.')
            return redirect(target)
        whatsapp = build_whatsapp_url(req.phone, question)
        if not whatsapp:
            messages.error(request, 'У покупателя не указан корректный номер WhatsApp.')
            return redirect(target)
        return redirect(whatsapp)
    if request.method == 'POST' and request.POST.get('action') == 'save_details':
        # Update only operator clarification fields; never alter geography or dispatch flags.
        description = request.POST.get('description', '').strip()
        article = request.POST.get('article', '').strip()
        vin = request.POST.get('vin', '').strip()
        if not description or len(description) > 10000 or len(article) > 100 or len(vin) > 32:
            messages.error(request, 'Заполните описание (до 10 000 символов), артикул до 100 и VIN до 32 символов.')
        else:
            Request.objects.filter(pk=pk).update(description=description, article=article, vin=vin)
            model_admin.log_change(request, req, 'Уточнены описание, артикул и VIN в рабочей карточке.')
            model_admin.log_change(request, req, STAGE_PREFIX + 'active')
            messages.success(request, 'Уточнения сохранены. Продавцы увидят их в персональной заявке.')
        return redirect(target)
    if request.method == 'POST' and request.POST.get('action') == 'note':
        note = request.POST.get('note', '').strip()
        if note and len(note) <= 2000:
            model_admin.log_change(request, req, 'Ручная обработка: ' + note)
            messages.success(request, 'Заметка сохранена.')
        else:
            messages.error(request, 'Введите заметку до 2000 символов.')
        return redirect(target)
    context = _base_context(active_nav='parts', breadcrumbs=[
        {'label': 'Заявки на запчасти', 'url': 'control_panel:parts_request_list'},
        {'label': f'Заявка №{pk}'},
    ], title=f'Заявка №{pk}')
    phone = ''.join(c for c in req.phone if c.isdigit())
    if len(phone) == 11 and phone.startswith('8'):
        phone = '7' + phone[1:]
    text = (f'Здравствуйте! Вы оставили на ZPT.KZ заявку №{pk}: {req.description}. '
            f'Автомобиль: {req.brand} {req.model}, {req.year or "год не указан"}. '
            'Уточните, пожалуйста, двигатель, количество деталей и пришлите VIN '
            'или фото старой детали с артикулом для точного подбора.')
    clarification_hint = ''
    if not req.vin.strip() and not req.article.strip() and len(req.description.split()) < 5:
        if 'форсунк' in req.description.casefold():
            clarification_hint = ('Недостаточно данных для точного подбора. Уточните двигатель, '
                                  'количество форсунок и VIN либо артикул старой детали.')
        else:
            clarification_hint = ('В заявке мало сведений для точного подбора. При необходимости '
                                  'уточните характеристики детали, количество и VIN либо артикул.')
    from core.services.buyer_receipt import receipt_text
    from core.phone_utils import build_whatsapp_url
    context.update(buyer_receipt_url=build_whatsapp_url(req.phone, receipt_text(req)),
                   buyer_receipt_sent=receipt_logs.order_by('-action_time').first())
    context.update(clarification_hint=clarification_hint, buyer_question=text)
    context.update(buyer_whatsapp=('https://wa.me/' + phone + '?text=' + quote(text, safe=''))
                   if 10 <= len(phone) <= 15 else '', buyer_phone=phone,
                   notes=LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Request),
                       object_id=str(pk), action_flag=CHANGE, change_message__startswith='Ручная обработка: '
                   ).select_related('user').order_by('-action_time')[:50])
    if request.method == 'POST':
        return manual_dispatch_admin_view(model_admin, request, pk,
            template_name='control_panel/parts_process.html', workspace_context=context, return_url=target)
    detail = get_parts_request_detail(pk)
    context.update(detail)
    stage = with_workspace_stage(Request.objects.filter(pk=pk)).values_list('workspace_stage', flat=True).get()
    context.update(request_obj=detail['parts_request'], workspace_stage=stage, workspace_label=STAGE_LABELS[stage],
        opened_count=sum(row.opened for row in detail['reactions']),
        prepared_count=sum(d.status == 'prepared' for d in detail['dispatches']))
    return render(request, 'control_panel/parts_process.html', context)
