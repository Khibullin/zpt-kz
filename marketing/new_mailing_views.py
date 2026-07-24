from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.views import View

from marketing.services.simple_mailing import (
    RECIPIENT_TYPE_CHOICES,
    RECIPIENT_TYPE_VALUES,
    SimpleMailingValidationError,
    get_available_brands,
    load_simple_mailing_draft,
    marketplace_brand_filter_enabled,
    resolve_simple_mailing_recipients,
    save_simple_mailing_draft,
    validate_brand_selection,
)
from marketing.services.simple_mailing.constants import (
    MARKETPLACE_BRAND_FILTER_AVAILABLE,
    RECIPIENT_TYPE_MARKETPLACE_BUYERS,
    RECIPIENT_TYPE_PARTS_REQUEST_BUYERS,
)
from marketing.views import MarketingCabinetMixin

DEFAULT_RECIPIENT_TYPE = RECIPIENT_TYPE_PARTS_REQUEST_BUYERS


class NewMailingView(MarketingCabinetMixin, View):
    template_name = 'marketing/new_mailing/index.html'
    active_nav = 'new_mailing'

    def get(self, request):
        return self._render_page(request)

    def post(self, request):
        action = request.POST.get('action', 'preview')
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

        if recipient_type not in RECIPIENT_TYPE_VALUES:
            messages.error(request, 'Выберите тип получателей.')
            return self._render_page(
                request,
                recipient_type=recipient_type,
                all_brands=all_brands,
                selected_brands=selected_brands,
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
            return self._render_page(
                request,
                recipient_type=recipient_type,
                all_brands=all_brands,
                selected_brands=selected_brands,
            )

        if action == 'continue':
            save_simple_mailing_draft(
                request.session,
                {
                    'recipient_type': recipient_type,
                    'all_brands': all_brands,
                    'brands': list(result.selection.brands),
                    'count': result.count,
                },
            )
            return redirect('marketing:new_mailing_message')

        return self._render_page(
            request,
            recipient_type=recipient_type,
            all_brands=all_brands,
            selected_brands=selected_brands,
            result=result,
        )

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

        brand_options = get_available_brands(recipient_type)
        brand_filter_enabled = marketplace_brand_filter_enabled(recipient_type)
        selected_brands = selected_brands or []
        type_labels = dict(RECIPIENT_TYPE_CHOICES)

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
            'brand_filter_enabled': brand_filter_enabled,
            'result': result,
            'show_preview': result is not None and bool(result.preview_rows),
        }
        return render(request, self.template_name, context)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET', 'POST'])


class NewMailingMessageView(MarketingCabinetMixin, View):
    template_name = 'marketing/new_mailing/message.html'
    active_nav = 'new_mailing'

    def get(self, request):
        draft = load_simple_mailing_draft(request.session)
        if not draft:
            messages.error(request, 'Сначала выберите группу получателей.')
            return redirect('marketing:new_mailing')

        recipient_type = draft.get('recipient_type') or DEFAULT_RECIPIENT_TYPE
        type_labels = dict(RECIPIENT_TYPE_CHOICES)
        brands_label = 'Все марки'
        if not draft.get('all_brands'):
            brands = draft.get('brands') or []
            brands_label = ', '.join(brands) if brands else '—'

        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            'draft': draft,
            'recipient_type_label': type_labels.get(recipient_type, recipient_type),
            'brands_label': brands_label,
            'recipient_count': draft.get('count', 0),
        }
        return render(request, self.template_name, context)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET'])
