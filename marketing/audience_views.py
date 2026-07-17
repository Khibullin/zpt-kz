from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from marketing.models import MarketingAudience
from marketing.services.audiences import (
    AUDIENCE_LIST_PAGE_SIZE,
    CONTACT_GROUPS,
    GROUP_SUBTYPE_MAP,
    build_registry,
    calculate_audience,
    criteria_summary,
    calculation_summary_lines,
    normalize_marketing_criteria,
    subtype_matches_group,
)
from marketing.services.audiences.filters import criteria_raw_from_request_post
from marketing.services.audiences.options import build_audience_filter_options
from marketing.services.audiences.validation import (
    CriteriaValidationError,
    validate_and_normalize_criteria,
    validate_request_post_fields,
)
from marketing.views import MarketingCabinetMixin

VALID_CONTACT_GROUPS = frozenset(key for key, _ in CONTACT_GROUPS)


def _wizard_field(request, name: str, *, default: str = '') -> str:
    if name in request.GET:
        return request.GET.get(name, default).strip()
    if name in request.POST:
        return request.POST.get(name, default).strip()
    return default


def _resolve_wizard_step(
    request,
    *,
    audience: MarketingAudience | None,
) -> tuple[int, str, str]:
    if audience:
        step = int(request.GET.get('step') or request.POST.get('step') or 3)
    else:
        step = int(request.GET.get('step') or request.POST.get('step') or 1)
    step = max(1, min(3, step))

    if audience:
        contact_group = audience.contact_group
        contact_subtype = audience.contact_subtype
        return step, contact_group, contact_subtype

    contact_group = _wizard_field(request, 'contact_group')
    contact_subtype = _wizard_field(request, 'contact_subtype')

    if contact_group and contact_group not in VALID_CONTACT_GROUPS:
        messages.error(request, 'Недопустимая группа контактов.')
        return 1, '', ''

    if step >= 2 and not contact_group:
        messages.error(request, 'Сначала выберите группу контактов.')
        return 1, '', ''

    if step >= 3:
        if not contact_subtype:
            messages.error(request, 'Выберите подтип аудитории.')
            return 2, contact_group, ''
        if not subtype_matches_group(contact_group, contact_subtype):
            messages.error(request, 'Подтип не соответствует выбранной группе. Выберите подтип заново.')
            return 2, contact_group, ''

    if contact_subtype and contact_group and not subtype_matches_group(contact_group, contact_subtype):
        messages.error(request, 'Подтип не соответствует выбранной группе. Выберите подтип заново.')
        return min(step, 2), contact_group, ''

    return step, contact_group, contact_subtype


def _filter_context(contact_group: str, contact_subtype: str, criteria: dict) -> dict:
    registry = build_registry()
    options = build_audience_filter_options(
        contact_group=contact_group,
        contact_subtype=contact_subtype,
        registry=registry,
    )
    return {
        'contact_groups': CONTACT_GROUPS,
        'group_subtypes': GROUP_SUBTYPE_MAP,
        'filter_options': options,
        'criteria': criteria,
        'contact_group': contact_group,
        'contact_subtype': contact_subtype,
    }


class AudienceListView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/audiences/list.html'
    active_nav = 'audiences'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        queryset = MarketingAudience.objects.select_related('created_by').order_by('-updated_at')
        paginator = Paginator(queryset, AUDIENCE_LIST_PAGE_SIZE)
        context['page_obj'] = paginator.get_page(self.request.GET.get('page'))
        return context


class AudienceFormMixin(MarketingCabinetMixin):
    template_name = 'marketing/audiences/form.html'
    active_nav = 'audiences'

    def get_audience(self) -> MarketingAudience | None:
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        audience = self.get_audience()
        if audience:
            contact_group = audience.contact_group
            contact_subtype = audience.contact_subtype
            criteria = normalize_marketing_criteria(
                audience.criteria,
                contact_group=contact_group,
                contact_subtype=contact_subtype,
            )
            context['audience'] = audience
            context['name'] = audience.name
            context['description'] = audience.description
            context['is_active'] = audience.is_active
        else:
            step, contact_group, contact_subtype = _resolve_wizard_step(
                self.request,
                audience=None,
            )
            criteria = normalize_marketing_criteria(
                {},
                contact_group=contact_group,
                contact_subtype=contact_subtype,
            )
            context['name'] = _wizard_field(self.request, 'name')
            context['description'] = _wizard_field(self.request, 'description')
            if 'is_active' in self.request.GET:
                context['is_active'] = self.request.GET.get('is_active') == 'on'
            else:
                context['is_active'] = self.request.POST.get('is_active', 'on') == 'on'
            context['step'] = step
        if audience:
            step, contact_group, contact_subtype = _resolve_wizard_step(
                self.request,
                audience=audience,
            )
            context['step'] = step
        context.update(_filter_context(contact_group, contact_subtype, criteria))
        context['current_subtypes'] = GROUP_SUBTYPE_MAP.get(contact_group, ())
        context['group_subtypes'] = GROUP_SUBTYPE_MAP
        if not audience:
            context.setdefault('step', 1)
        else:
            context.setdefault('step', 3)
        context['calculation'] = kwargs.get('calculation')
        context['calculation_lines'] = kwargs.get('calculation_lines', [])
        return context


def _parse_audience_post(request, *, contact_group: str, contact_subtype: str) -> dict:
    validate_request_post_fields(
        request.POST,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )
    raw = criteria_raw_from_request_post(
        request.POST,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )
    return validate_and_normalize_criteria(
        raw,
        contact_group=contact_group,
        contact_subtype=contact_subtype,
    )


class AudienceCreateView(AudienceFormMixin, TemplateView):
    def post(self, request, *args, **kwargs):
        action = request.POST.get('action', 'save')
        contact_group = request.POST.get('contact_group', '').strip()
        contact_subtype = request.POST.get('contact_subtype', '').strip()
        if not subtype_matches_group(contact_group, contact_subtype):
            messages.error(request, 'Выберите корректную группу и подтип аудитории.')
            return self.render_to_response(self.get_context_data())

        try:
            criteria = _parse_audience_post(
                request,
                contact_group=contact_group,
                contact_subtype=contact_subtype,
            )
        except CriteriaValidationError as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())
        if action == 'calculate':
            calculation = calculate_audience(
                contact_group=contact_group,
                contact_subtype=contact_subtype,
                criteria=criteria,
            )
            return self.render_to_response(
                self.get_context_data(
                    calculation=calculation,
                    calculation_lines=calculation_summary_lines(calculation),
                ),
            )

        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название аудитории.')
            return self.render_to_response(self.get_context_data())

        audience = MarketingAudience.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            contact_group=contact_group,
            contact_subtype=contact_subtype,
            criteria=criteria,
            is_active=request.POST.get('is_active', 'on') == 'on',
            created_by=request.user,
        )
        calculation = calculate_audience(
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
            criteria=audience.criteria,
        )
        audience.last_calculated_at = timezone.now()
        audience.last_matched_count = calculation.matched_count
        audience.last_eligible_count = calculation.eligible_count
        audience.save(
            update_fields=[
                'last_calculated_at',
                'last_matched_count',
                'last_eligible_count',
            ],
        )
        messages.success(request, f'Аудитория «{audience.name}» сохранена.')
        return redirect('marketing:audience_detail', pk=audience.pk)


class AudienceDetailView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/audiences/detail.html'
    active_nav = 'audiences'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        audience = get_object_or_404(
            MarketingAudience.objects.select_related('created_by'),
            pk=kwargs['pk'],
        )
        calculation = calculate_audience(
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
            criteria=audience.criteria,
        )
        context['audience'] = audience
        context['criteria_summary'] = criteria_summary(
            audience.criteria,
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
        )
        context['calculation'] = calculation
        context['calculation_lines'] = calculation_summary_lines(calculation)
        return context


class AudienceUpdateView(AudienceFormMixin, TemplateView):
    def get_audience(self) -> MarketingAudience | None:
        return get_object_or_404(MarketingAudience, pk=self.kwargs['pk'])

    def post(self, request, *args, **kwargs):
        audience = self.get_audience()
        action = request.POST.get('action', 'save')
        contact_group = request.POST.get('contact_group', audience.contact_group).strip()
        contact_subtype = request.POST.get('contact_subtype', audience.contact_subtype).strip()
        if not subtype_matches_group(contact_group, contact_subtype):
            messages.error(request, 'Выберите корректную группу и подтип аудитории.')
            return self.render_to_response(self.get_context_data())

        try:
            criteria = _parse_audience_post(
                request,
                contact_group=contact_group,
                contact_subtype=contact_subtype,
            )
        except CriteriaValidationError as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())
        if action == 'calculate':
            calculation = calculate_audience(
                contact_group=contact_group,
                contact_subtype=contact_subtype,
                criteria=criteria,
            )
            return self.render_to_response(
                self.get_context_data(
                    calculation=calculation,
                    calculation_lines=calculation_summary_lines(calculation),
                ),
            )

        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Укажите название аудитории.')
            return self.render_to_response(self.get_context_data())

        audience.name = name
        audience.description = request.POST.get('description', '').strip()
        audience.contact_group = contact_group
        audience.contact_subtype = contact_subtype
        audience.criteria = criteria
        audience.is_active = request.POST.get('is_active', 'on') == 'on'
        audience.save()
        calculation = calculate_audience(
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
            criteria=audience.criteria,
        )
        audience.last_calculated_at = timezone.now()
        audience.last_matched_count = calculation.matched_count
        audience.last_eligible_count = calculation.eligible_count
        audience.save(
            update_fields=[
                'name',
                'description',
                'contact_group',
                'contact_subtype',
                'criteria',
                'is_active',
                'updated_at',
                'last_calculated_at',
                'last_matched_count',
                'last_eligible_count',
            ],
        )
        messages.success(request, f'Аудитория «{audience.name}» обновлена.')
        return redirect('marketing:audience_detail', pk=audience.pk)


class AudienceCalculateView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        audience = get_object_or_404(MarketingAudience, pk=pk)
        calculation = calculate_audience(
            contact_group=audience.contact_group,
            contact_subtype=audience.contact_subtype,
            criteria=audience.criteria,
        )
        audience.last_calculated_at = timezone.now()
        audience.last_matched_count = calculation.matched_count
        audience.last_eligible_count = calculation.eligible_count
        audience.save(
            update_fields=[
                'last_calculated_at',
                'last_matched_count',
                'last_eligible_count',
            ],
        )
        messages.success(request, 'Расчёт аудитории обновлён.')
        return redirect('marketing:audience_detail', pk=audience.pk)


class AudienceCopyView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        source = get_object_or_404(MarketingAudience, pk=pk)
        copy_name = f'{source.name} (копия)'
        suffix = 2
        while MarketingAudience.objects.filter(name=copy_name).exists():
            copy_name = f'{source.name} (копия {suffix})'
            suffix += 1
        copy = MarketingAudience.objects.create(
            name=copy_name,
            description=source.description,
            contact_group=source.contact_group,
            contact_subtype=source.contact_subtype,
            criteria=source.criteria,
            is_active=source.is_active,
            created_by=request.user,
            last_calculated_at=source.last_calculated_at,
            last_matched_count=source.last_matched_count,
            last_eligible_count=source.last_eligible_count,
        )
        messages.success(request, f'Создана копия: «{copy.name}».')
        return redirect('marketing:audience_edit', pk=copy.pk)


class AudienceDeleteView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/audiences/confirm_delete.html'
    active_nav = 'audiences'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        context['audience'] = get_object_or_404(MarketingAudience, pk=kwargs['pk'])
        return context

    def post(self, request, pk):
        audience = get_object_or_404(MarketingAudience, pk=pk)
        if request.POST.get('confirm') != 'yes':
            messages.error(request, 'Подтвердите удаление аудитории.')
            return redirect('marketing:audience_delete', pk=pk)
        name = audience.name
        audience.delete()
        messages.success(request, f'Аудитория «{name}» удалена.')
        return redirect('marketing:audiences')
