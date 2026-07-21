from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from marketing.models import MarketingWhatsAppTemplate
from marketing.services.templates.constants import (
    CATEGORY_MARKETING,
    META_STATUS_CHOICES,
    TEMPLATE_BUSINESS_PURPOSE_CHOICES,
    TEMPLATE_LIST_PAGE_SIZE,
)
from marketing.services.templates.preview import render_template_preview_text
from marketing.services.templates.selectors import (
    copy_template,
    filter_template_list,
    template_list_queryset,
)
from marketing.services.templates.validation import (
    TemplateValidationError,
    raw_buttons_from_post,
    raw_variables_from_post,
    validate_allowed_purposes,
    validate_buttons,
    validate_language_code,
    validate_meta_template_name,
    validate_variables,
)
from marketing.views import MarketingCabinetMixin


def _template_form_values(request, template: MarketingWhatsAppTemplate | None) -> dict:
    if request.method == 'POST':
        allowed_raw = request.POST.getlist('allowed_purposes')
        return {
            'name': request.POST.get('name', '').strip(),
            'meta_template_name': request.POST.get('meta_template_name', '').strip(),
            'language_code': request.POST.get('language_code', 'ru').strip(),
            'meta_status': request.POST.get('meta_status', '').strip(),
            'is_active': request.POST.get('is_active') == 'on',
            'allow_test_campaign': request.POST.get('allow_test_campaign') == 'on',
            'header_text': request.POST.get('header_text', '').strip(),
            'body_text': request.POST.get('body_text', '').strip(),
            'footer_text': request.POST.get('footer_text', '').strip(),
            'internal_notes': request.POST.get('internal_notes', '').strip(),
            'meta_template_id': request.POST.get('meta_template_id', '').strip(),
            'allowed_purposes': [value.strip() for value in allowed_raw if value.strip()],
            'variables': raw_variables_from_post(request.POST),
            'buttons': raw_buttons_from_post(request.POST),
        }
    if template is None:
        return {
            'name': '',
            'meta_template_name': '',
            'language_code': 'ru',
            'meta_status': 'unknown',
            'is_active': True,
            'allow_test_campaign': False,
            'header_text': '',
            'body_text': '',
            'footer_text': '',
            'internal_notes': '',
            'meta_template_id': '',
            'allowed_purposes': [],
            'variables': [],
            'buttons': [],
        }
    return {
        'name': template.name,
        'meta_template_name': template.meta_template_name,
        'language_code': template.language_code,
        'meta_status': template.meta_status,
        'is_active': template.is_active,
        'allow_test_campaign': template.allow_test_campaign,
        'header_text': template.header_text,
        'body_text': template.body_text,
        'footer_text': template.footer_text,
        'internal_notes': template.internal_notes,
        'meta_template_id': template.meta_template_id,
        'allowed_purposes': list(template.allowed_purposes),
        'variables': list(template.variables),
        'buttons': list(template.buttons),
    }


def _save_template_from_post(request, template: MarketingWhatsAppTemplate | None):
    values = _template_form_values(request, template)
    if not values['name']:
        raise TemplateValidationError('Укажите внутреннее название шаблона.')
    values['meta_template_name'] = validate_meta_template_name(values['meta_template_name'])
    values['language_code'] = validate_language_code(values['language_code'])
    values['allowed_purposes'] = validate_allowed_purposes(values['allowed_purposes'])
    values['variables'] = validate_variables(values['variables'])
    values['buttons'] = validate_buttons(values['buttons'])
    if template is None:
        template = MarketingWhatsAppTemplate(created_by=request.user)
    template.name = values['name']
    template.meta_template_name = values['meta_template_name']
    template.language_code = values['language_code']
    template.category = CATEGORY_MARKETING
    template.meta_status = values['meta_status']
    template.is_active = values['is_active']
    template.allow_test_campaign = values['allow_test_campaign']
    template.header_text = values['header_text']
    template.body_text = values['body_text']
    template.footer_text = values['footer_text']
    template.internal_notes = values['internal_notes']
    template.meta_template_id = values['meta_template_id']
    template.allowed_purposes = values['allowed_purposes']
    template.variables = values['variables']
    template.buttons = values['buttons']
    template.save()
    return template


class TemplateListView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/templates/list.html'
    active_nav = 'templates'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        queryset = filter_template_list(template_list_queryset(), self.request.GET)
        paginator = Paginator(queryset, TEMPLATE_LIST_PAGE_SIZE)
        context['page_obj'] = paginator.get_page(self.request.GET.get('page'))
        context['meta_status_choices'] = META_STATUS_CHOICES
        context['purpose_choices'] = TEMPLATE_BUSINESS_PURPOSE_CHOICES
        context['filters'] = {
            'meta_status': self.request.GET.get('meta_status', ''),
            'purpose': self.request.GET.get('purpose', ''),
            'language_code': self.request.GET.get('language_code', ''),
            'is_active': self.request.GET.get('is_active', ''),
        }
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        context['pagination_querystring'] = query_params.urlencode()
        return context


class TemplateFormMixin(MarketingCabinetMixin):
    template_name = 'marketing/templates/form.html'
    active_nav = 'templates'

    def get_template_obj(self) -> MarketingWhatsAppTemplate | None:
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        template = self.get_template_obj()
        context['template_obj'] = template
        context['is_edit'] = template is not None
        context['form_values'] = _template_form_values(self.request, template)
        context['meta_status_choices'] = META_STATUS_CHOICES
        context['purpose_choices'] = TEMPLATE_BUSINESS_PURPOSE_CHOICES
        return context


class TemplateCreateView(TemplateFormMixin, TemplateView):
    def post(self, request, *args, **kwargs):
        try:
            template = _save_template_from_post(request, None)
        except (TemplateValidationError, ValidationError) as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())
        messages.success(request, f'Шаблон «{template.name}» создан.')
        return redirect('marketing:template_detail', pk=template.pk)


class TemplateDetailView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/templates/detail.html'
    active_nav = 'templates'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        template = get_object_or_404(
            MarketingWhatsAppTemplate.objects.select_related('created_by'),
            pk=kwargs['pk'],
        )
        context['template_obj'] = template
        context['preview'] = render_template_preview_text(template)
        context['campaign_count'] = template.campaigns.count()
        return context


class TemplateUpdateView(TemplateFormMixin, TemplateView):
    def get_template_obj(self) -> MarketingWhatsAppTemplate | None:
        return get_object_or_404(MarketingWhatsAppTemplate, pk=self.kwargs['pk'])

    def post(self, request, *args, **kwargs):
        template = self.get_template_obj()
        try:
            template = _save_template_from_post(request, template)
        except (TemplateValidationError, ValidationError) as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())
        messages.success(request, f'Шаблон «{template.name}» обновлён.')
        return redirect('marketing:template_detail', pk=template.pk)


class TemplateCopyView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        source = get_object_or_404(MarketingWhatsAppTemplate, pk=pk)
        copy = copy_template(source, created_by=request.user)
        messages.success(request, f'Создана копия: «{copy.name}».')
        return redirect('marketing:template_detail', pk=copy.pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class TemplateActivateView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=pk)
        template.is_active = True
        template.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'Шаблон «{template.name}» активирован.')
        return redirect('marketing:template_detail', pk=pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class TemplateDeactivateView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=pk)
        template.is_active = False
        template.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'Шаблон «{template.name}» деактивирован.')
        return redirect('marketing:template_detail', pk=pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class TemplateDeleteView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/templates/confirm_delete.html'
    active_nav = 'templates'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        context['template_obj'] = get_object_or_404(MarketingWhatsAppTemplate, pk=kwargs['pk'])
        return context

    def dispatch(self, request, *args, **kwargs):
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=kwargs['pk'])
        if template.campaigns.exists():
            messages.error(request, 'Нельзя удалить шаблон, используемый кампанией.')
            return redirect('marketing:template_detail', pk=template.pk)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=pk)
        if template.campaigns.exists():
            messages.error(request, 'Нельзя удалить шаблон, используемый кампанией.')
            return redirect('marketing:template_detail', pk=pk)
        if request.POST.get('confirm') != 'yes':
            messages.error(request, 'Подтвердите удаление шаблона.')
            return redirect('marketing:template_delete', pk=pk)
        name = template.name
        template.delete()
        messages.success(request, f'Шаблон «{name}» удалён.')
        return redirect('marketing:templates')
