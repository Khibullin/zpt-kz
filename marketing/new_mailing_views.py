from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from marketing.models import MarketingWhatsAppTemplate
from marketing.services.campaigns.send_settings import marketing_live_whatsapp_send_enabled
from marketing.services.simple_mailing import (
    RECIPIENT_TYPE_CHOICES,
    RECIPIENT_TYPE_VALUES,
    SimpleMailingValidationError,
    build_template_cards,
    clear_preview_state,
    get_available_brands,
    has_brand_selection,
    load_draft_template_id,
    load_simple_mailing_draft,
    marketplace_brand_filter_enabled,
    normalize_brand_selection,
    preview_matches,
    render_simple_mailing_template_preview,
    resolve_selected_template,
    resolve_simple_mailing_recipients,
    save_preview_state,
    save_simple_mailing_draft,
    save_template_to_draft,
    template_still_available,
    validate_brand_selection,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
)
from marketing.services.simple_mailing.preview import build_selection_key
from marketing.services.simple_mailing.draft import ensure_launch_key_in_draft, update_draft_count
from marketing.services.simple_mailing.launch import (
    SimpleMailingCountChangedError,
    SimpleMailingLaunchError,
    launch_simple_mailing,
)
from marketing.services.templates.validation import TemplateValidationError
from marketing.views import MarketingCabinetMixin

DEFAULT_RECIPIENT_TYPE = RECIPIENT_TYPE_PARTS_REQUEST_BUYERS


def _draft_summary_context(draft: dict) -> dict:
    recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
    type_labels = dict(RECIPIENT_TYPE_CHOICES)
    brands_label = 'Все марки'
    if not draft.get('all_brands'):
        brands = draft.get('brands') or []
        brands_label = ', '.join(brands) if brands else '—'

    return {
        'draft': draft,
        'recipient_type_label': type_labels.get(recipient_type, recipient_type),
        'brands_label': brands_label,
        'recipient_count': draft.get('count', 0),
    }


def _require_recipient_draft(request):
    draft = load_simple_mailing_draft(request.session)
    if not draft:
        messages.error(request, 'Сначала выберите получателей.')
        return None
    return draft


class NewMailingView(MarketingCabinetMixin, View):
    template_name = 'marketing/new_mailing/index.html'
    active_nav = 'new_mailing'

    def get(self, request):
        clear_preview_state(request.session)
        return self._render_page(request)

    def post(self, request):
        action = request.POST.get('action', 'preview')
        recipient_type, all_brands, selected_brands = self._parse_selection(request)

        if recipient_type not in RECIPIENT_TYPE_VALUES:
            messages.error(request, 'Выберите тип получателей.')
            clear_preview_state(request.session)
            return self._render_page(
                request,
                recipient_type=recipient_type,
                all_brands=all_brands,
                selected_brands=selected_brands,
            )

        if not has_brand_selection(
            recipient_type=recipient_type,
            all_brands=all_brands,
            brands=selected_brands,
        ):
            messages.error(request, 'Выберите «Все марки» или одну и более марок.')
            clear_preview_state(request.session)
            return self._render_page(
                request,
                recipient_type=recipient_type,
                all_brands=all_brands,
                selected_brands=selected_brands,
            )

        selection_key = build_selection_key(
            recipient_type=recipient_type,
            all_brands=all_brands,
            brands=selected_brands,
        )

        try:
            validated_brands = validate_brand_selection(
                recipient_type=recipient_type,
                all_brands=all_brands,
                brands=selected_brands,
            )
            result = resolve_simple_mailing_recipients(
                recipient_type=recipient_type,
                all_brands=all_brands,
                brands=validated_brands,
            )
        except SimpleMailingValidationError as exc:
            messages.error(request, str(exc))
            clear_preview_state(request.session)
            return self._render_page(
                request,
                recipient_type=recipient_type,
                all_brands=all_brands,
                selected_brands=selected_brands,
            )

        if action == 'continue':
            if not preview_matches(request.session, selection_key):
                messages.error(
                    request,
                    'Фильтры изменились. Сначала нажмите «Показать количество».',
                )
                return self._render_page(
                    request,
                    recipient_type=recipient_type,
                    all_brands=all_brands,
                    selected_brands=selected_brands,
                )
            if result.count <= 0:
                messages.error(request, 'Получатели не найдены. Измените фильтры и попробуйте снова.')
                clear_preview_state(request.session)
                return self._render_page(
                    request,
                    recipient_type=recipient_type,
                    all_brands=all_brands,
                    selected_brands=selected_brands,
                    result=result,
                )
            save_simple_mailing_draft(
                request.session,
                {
                    'recipient_type': recipient_type,
                    'all_brands': all_brands,
                    'brands': list(result.selection.brands),
                    'count': result.count,
                },
            )
            clear_preview_state(request.session)
            return redirect('marketing:new_mailing_message')

        save_preview_state(
            request.session,
            selection_key=selection_key,
            count=result.count,
        )
        return self._render_page(
            request,
            recipient_type=recipient_type,
            all_brands=all_brands,
            selected_brands=selected_brands,
            result=result,
        )

    def _parse_selection(self, request) -> tuple[str, bool, list[str]]:
        recipient_type = (request.POST.get('recipient_type') or '').strip()
        all_brands = request.POST.get('all_brands') == '1'
        selected_brands = [
            value.strip()
            for value in request.POST.getlist('brands')
            if str(value).strip()
        ]

        if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS and not MARKETPLACE_BRAND_FILTER_AVAILABLE:
            all_brands = True
            selected_brands = []

        all_brands, selected_brands = normalize_brand_selection(
            all_brands=all_brands,
            brands=selected_brands,
        )
        return recipient_type, all_brands, selected_brands

    def _render_page(
        self,
        request,
        *,
        recipient_type: str | None = None,
        all_brands: bool = False,
        selected_brands: list[str] | None = None,
        result=None,
    ):
        recipient_type = recipient_type or request.GET.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        if recipient_type not in RECIPIENT_TYPE_VALUES:
            recipient_type = DEFAULT_RECIPIENT_TYPE

        if recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS and not MARKETPLACE_BRAND_FILTER_AVAILABLE:
            all_brands = True
            selected_brands = []

        brand_options = get_available_brands(recipient_type)
        brand_filter_enabled = marketplace_brand_filter_enabled(recipient_type)
        selected_brands = selected_brands or []
        type_labels = dict(RECIPIENT_TYPE_CHOICES)

        can_calculate = has_brand_selection(
            recipient_type=recipient_type,
            all_brands=all_brands,
            brands=selected_brands,
        )
        can_continue = result is not None and result.count > 0

        if all_brands or (
            recipient_type == RECIPIENT_TYPE_MARKETPLACE_BUYERS and not brand_filter_enabled
        ):
            selected_brands_label = 'Все марки'
        else:
            selected_brands_label = str(len(selected_brands))

        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            'recipient_type': recipient_type,
            'recipient_type_label': type_labels.get(recipient_type, recipient_type),
            'recipient_type_choices': RECIPIENT_TYPE_CHOICES,
            'brand_options': brand_options,
            'all_brands': all_brands,
            'selected_brands': selected_brands,
            'selected_brands_label': selected_brands_label,
            'brand_filter_enabled': brand_filter_enabled,
            'result': result,
            'can_calculate': can_calculate,
            'can_continue': can_continue,
            'count_display': str(result.count) if result is not None else '—',
            'show_preview': result is not None and bool(result.preview_rows),
        }
        return render(request, self.template_name, context)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET', 'POST'])


class NewMailingMessageView(MarketingCabinetMixin, View):
    template_name = 'marketing/new_mailing/message.html'
    active_nav = 'new_mailing'

    def get(self, request):
        draft = _require_recipient_draft(request)
        if draft is None:
            return redirect('marketing:new_mailing')

        recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        template_cards = build_template_cards(recipient_type)
        selected_template_id = load_draft_template_id(request.session)

        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            **_draft_summary_context(draft),
            'current_step': 2,
            'template_cards': template_cards,
            'selected_template_id': selected_template_id,
            'has_compatible_templates': bool(template_cards),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        draft = _require_recipient_draft(request)
        if draft is None:
            return redirect('marketing:new_mailing')

        recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        template_id = (request.POST.get('template_id') or '').strip()

        try:
            template = resolve_selected_template(template_id, recipient_type=recipient_type)
        except TemplateValidationError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:new_mailing_message')

        if template is None:
            messages.error(request, 'Выберите сообщение для рассылки.')
            return redirect('marketing:new_mailing_message')

        save_template_to_draft(request.session, template.pk)
        return redirect('marketing:new_mailing_confirm')

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET', 'POST'])


class NewMailingConfirmView(MarketingCabinetMixin, View):
    template_name = 'marketing/new_mailing/confirm.html'
    active_nav = 'new_mailing'

    def get(self, request):
        draft = _require_recipient_draft(request)
        if draft is None:
            return redirect('marketing:new_mailing')

        template_id = load_draft_template_id(request.session)
        if template_id is None:
            messages.error(request, 'Сначала выберите сообщение.')
            return redirect('marketing:new_mailing_message')

        template = get_object_or_404(MarketingWhatsAppTemplate, pk=template_id)
        recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        if not template_still_available(template, recipient_type=recipient_type):
            messages.error(
                request,
                'Выбранный шаблон больше недоступен. Выберите другой.',
            )
            return redirect('marketing:new_mailing_message')

        preview = render_simple_mailing_template_preview(template)
        launch_key = ensure_launch_key_in_draft(request.session)
        send_enabled = marketing_live_whatsapp_send_enabled()
        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            **_draft_summary_context(draft),
            'current_step': 3,
            'template': template,
            'template_preview': preview,
            'send_enabled': send_enabled,
            'launch_key': launch_key,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        draft = _require_recipient_draft(request)
        if draft is None:
            return redirect('marketing:new_mailing')

        if not marketing_live_whatsapp_send_enabled():
            messages.error(request, 'Отправка отключена. Режим: OFF.')
            return redirect('marketing:new_mailing_confirm')

        template_id = load_draft_template_id(request.session)
        if template_id is None:
            messages.error(request, 'Сначала выберите сообщение.')
            return redirect('marketing:new_mailing_message')

        template = get_object_or_404(MarketingWhatsAppTemplate, pk=template_id)
        recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        if not template_still_available(template, recipient_type=recipient_type):
            messages.error(
                request,
                'Выбранный шаблон больше недоступен. Выберите другой.',
            )
            return redirect('marketing:new_mailing_message')

        launch_key = ensure_launch_key_in_draft(request.session)
        try:
            result = launch_simple_mailing(
                draft=draft,
                template=template,
                created_by=request.user,
                launch_key=launch_key,
            )
        except SimpleMailingCountChangedError as exc:
            update_draft_count(request.session, exc.actual)
            messages.error(
                request,
                (
                    f'Состав получателей изменился. Было: {exc.expected}. '
                    f'Сейчас: {exc.actual}. Проверьте рассылку ещё раз.'
                ),
            )
            return redirect('marketing:new_mailing_confirm')
        except SimpleMailingLaunchError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:new_mailing_confirm')

        if result.idempotent_replay:
            messages.info(
                request,
                f'Рассылка уже была создана (run #{result.send_run_id}).',
            )
        else:
            messages.success(
                request,
                (
                    f'Рассылка запущена (run #{result.send_run_id}): '
                    f'{result.queued_count} сообщений в очереди.'
                ),
            )
        return redirect('marketing:history')

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET', 'POST'])
