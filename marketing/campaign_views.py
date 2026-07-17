from __future__ import annotations

import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from marketing.models import MarketingAudience, MarketingCampaign
from marketing.services.audiences import criteria_summary
from marketing.services.campaigns.compatibility import compatible_audiences_for_purpose
from marketing.services.campaigns.constants import (
    CAMPAIGN_LIST_PAGE_SIZE,
    CAMPAIGN_PREVIEW_LIMIT,
    CAMPAIGN_PURPOSE_CHOICES,
    CAMPAIGN_STATUS_CHOICES,
    STATUS_ARCHIVED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
)
from marketing.services.campaigns.preparation import (
    clear_campaign_snapshot,
    copy_campaign,
    prepare_campaign_snapshot,
)
from marketing.services.campaigns.selectors import (
    campaign_authors_queryset,
    campaign_list_queryset,
    campaign_recipient_preview,
    filter_campaign_list,
)
from marketing.services.campaigns.summaries import campaign_display_status_label
from marketing.services.campaigns.validation import (
    CampaignValidationError,
    resolve_audience_from_post,
    validate_campaign_deletable,
    validate_campaign_editable,
    validate_campaign_form_fields,
)
from marketing.views import MarketingCabinetMixin


def _audience_option_payload(audience: MarketingAudience) -> dict:
    return {
        'id': audience.pk,
        'name': audience.name,
        'contact_group': audience.contact_group,
        'contact_subtype': audience.contact_subtype,
        'contact_group_label': audience.contact_group_label,
        'contact_subtype_label': audience.contact_subtype_label,
        'last_calculated_at': (
            timezone.localtime(audience.last_calculated_at).strftime('%d.%m.%Y %H:%M')
            if audience.last_calculated_at
            else ''
        ),
        'last_matched_count': audience.last_matched_count,
        'last_eligible_count': audience.last_eligible_count,
        'updated_at': timezone.localtime(audience.updated_at).strftime('%d.%m.%Y %H:%M'),
        'never_calculated': audience.last_calculated_at is None,
    }


class CampaignListView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/list.html'
    active_nav = 'campaigns'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        queryset = filter_campaign_list(campaign_list_queryset(), self.request.GET)
        paginator = Paginator(queryset, CAMPAIGN_LIST_PAGE_SIZE)
        context['page_obj'] = paginator.get_page(self.request.GET.get('page'))
        context['status_choices'] = CAMPAIGN_STATUS_CHOICES
        context['purpose_choices'] = CAMPAIGN_PURPOSE_CHOICES
        context['audiences'] = MarketingAudience.objects.filter(is_active=True).order_by('name')
        context['authors'] = campaign_authors_queryset()
        context['filters'] = {
            'status': self.request.GET.get('status', ''),
            'purpose': self.request.GET.get('purpose', ''),
            'audience': self.request.GET.get('audience', ''),
            'author': self.request.GET.get('author', ''),
            'created_from': self.request.GET.get('created_from', ''),
            'created_to': self.request.GET.get('created_to', ''),
        }
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        context['pagination_querystring'] = query_params.urlencode()
        return context


class CampaignFormMixin(MarketingCabinetMixin):
    template_name = 'marketing/campaigns/form.html'
    active_nav = 'campaigns'

    def get_campaign(self) -> MarketingCampaign | None:
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        campaign = self.get_campaign()
        context['campaign'] = campaign
        context['purpose_choices'] = CAMPAIGN_PURPOSE_CHOICES
        context['is_edit'] = campaign is not None
        selected_purpose = (
            self.request.POST.get('purpose')
            or (campaign.purpose if campaign else '')
            or self.request.GET.get('purpose', '')
        ).strip()
        context['selected_purpose'] = selected_purpose
        if selected_purpose:
            compatible = compatible_audiences_for_purpose(selected_purpose)
        else:
            compatible = MarketingAudience.objects.filter(is_active=True).order_by('name')
        context['compatible_audiences'] = compatible
        context['audiences_json'] = json.dumps(
            [_audience_option_payload(audience) for audience in compatible],
            ensure_ascii=False,
        )
        selected_audience_id = (
            self.request.POST.get('audience')
            or (str(campaign.audience_id) if campaign else '')
        ).strip()
        context['selected_audience_id'] = selected_audience_id
        selected_audience = None
        if selected_audience_id.isdigit():
            selected_audience = compatible.filter(pk=int(selected_audience_id)).first()
        context['selected_audience'] = selected_audience
        context['form_values'] = {
            'name': self.request.POST.get('name', campaign.name if campaign else ''),
            'description': self.request.POST.get(
                'description',
                campaign.description if campaign else '',
            ),
            'is_active': (
                self.request.POST.get('is_active', 'on' if (campaign is None or campaign.is_active) else '')
                == 'on'
            ),
        }
        return context


class CampaignCreateView(CampaignFormMixin, TemplateView):
    def post(self, request, *args, **kwargs):
        name = request.POST.get('name', '').strip()
        purpose = request.POST.get('purpose', '').strip()
        audience_id = request.POST.get('audience', '').strip()
        try:
            audience = resolve_audience_from_post(audience_id, purpose=purpose)
            validate_campaign_form_fields(
                name=name,
                purpose=purpose,
                audience=audience,
                audience_id=audience_id,
            )
        except CampaignValidationError as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())

        campaign = MarketingCampaign.objects.create(
            name=name,
            description=request.POST.get('description', '').strip(),
            audience=audience,
            purpose=purpose,
            is_active=request.POST.get('is_active', 'on') == 'on',
            created_by=request.user,
        )
        messages.success(request, f'Кампания «{campaign.name}» создана.')
        return redirect('marketing:campaign_detail', pk=campaign.pk)


class CampaignDetailView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/detail.html'
    active_nav = 'campaigns'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        campaign = get_object_or_404(
            MarketingCampaign.objects.select_related('audience', 'created_by'),
            pk=kwargs['pk'],
        )
        preview_filter = self.request.GET.get('preview', 'all').strip() or 'all'
        if preview_filter not in {'all', 'eligible', 'excluded'}:
            preview_filter = 'all'
        context['campaign'] = campaign
        context['display_status_label'] = campaign_display_status_label(campaign)
        context['criteria_summary'] = criteria_summary(
            campaign.audience.criteria,
            contact_group=campaign.audience.contact_group,
            contact_subtype=campaign.audience.contact_subtype,
        )
        context['preview_filter'] = preview_filter
        context['preview_recipients'] = campaign_recipient_preview(
            campaign,
            preview_filter=preview_filter,
        )
        context['preview_limit'] = CAMPAIGN_PREVIEW_LIMIT
        context['snapshot_stale'] = campaign.is_snapshot_stale()
        return context


class CampaignUpdateView(CampaignFormMixin, TemplateView):
    def get_campaign(self) -> MarketingCampaign | None:
        return get_object_or_404(
            MarketingCampaign.objects.select_related('audience'),
            pk=self.kwargs['pk'],
        )

    def dispatch(self, request, *args, **kwargs):
        campaign = get_object_or_404(MarketingCampaign, pk=kwargs['pk'])
        if not campaign.is_editable:
            messages.error(request, 'Кампания не может быть изменена в текущем статусе.')
            return redirect('marketing:campaign_detail', pk=campaign.pk)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        campaign = self.get_campaign()
        name = request.POST.get('name', '').strip()
        purpose = request.POST.get('purpose', '').strip()
        audience_id = request.POST.get('audience', '').strip()
        try:
            validate_campaign_editable(campaign)
            audience = resolve_audience_from_post(audience_id, purpose=purpose)
            validate_campaign_form_fields(
                name=name,
                purpose=purpose,
                audience=audience,
                audience_id=audience_id,
            )
        except CampaignValidationError as exc:
            messages.error(request, str(exc))
            return self.render_to_response(self.get_context_data())

        audience_changed = (
            campaign.audience_id != audience.pk
            or campaign.purpose != purpose
        )
        campaign.name = name
        campaign.description = request.POST.get('description', '').strip()
        campaign.purpose = purpose
        campaign.audience = audience
        campaign.is_active = request.POST.get('is_active', 'on') == 'on'
        if audience_changed:
            clear_campaign_snapshot(campaign)
        campaign.save()
        messages.success(request, f'Кампания «{campaign.name}» обновлена.')
        return redirect('marketing:campaign_detail', pk=campaign.pk)


class CampaignPrepareView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        try:
            prepare_campaign_snapshot(pk)
        except CampaignValidationError as exc:
            messages.error(request, str(exc))
        except MarketingCampaign.DoesNotExist:
            messages.error(request, 'Кампания не найдена.')
        else:
            messages.success(request, 'Снимок получателей подготовлен.')
        return redirect('marketing:campaign_detail', pk=pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class CampaignCopyView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        source = get_object_or_404(
            MarketingCampaign.objects.select_related('audience'),
            pk=pk,
        )
        copy = copy_campaign(source, created_by=request.user)
        messages.success(request, f'Создана копия: «{copy.name}».')
        return redirect('marketing:campaign_detail', pk=copy.pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class CampaignCancelView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        campaign = get_object_or_404(MarketingCampaign, pk=pk)
        if campaign.status == STATUS_CANCELLED:
            messages.info(request, 'Кампания уже отменена.')
        else:
            campaign.status = STATUS_CANCELLED
            campaign.cancelled_at = timezone.now()
            campaign.save(update_fields=['status', 'cancelled_at', 'updated_at'])
            messages.success(request, 'Кампания отменена.')
        return redirect('marketing:campaign_detail', pk=pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class CampaignArchiveView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        campaign = get_object_or_404(MarketingCampaign, pk=pk)
        if campaign.status == STATUS_ARCHIVED:
            messages.info(request, 'Кампания уже в архиве.')
        else:
            campaign.status = STATUS_ARCHIVED
            campaign.archived_at = timezone.now()
            campaign.save(update_fields=['status', 'archived_at', 'updated_at'])
            messages.success(request, 'Кампания архивирована.')
        return redirect('marketing:campaign_detail', pk=pk)

    def get(self, request, pk):
        return HttpResponseNotAllowed(['POST'])


class CampaignDeleteView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/confirm_delete.html'
    active_nav = 'campaigns'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_nav_context())
        context['campaign'] = get_object_or_404(MarketingCampaign, pk=kwargs['pk'])
        return context

    def dispatch(self, request, *args, **kwargs):
        campaign = get_object_or_404(MarketingCampaign, pk=kwargs['pk'])
        try:
            validate_campaign_deletable(campaign)
        except CampaignValidationError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:campaign_detail', pk=campaign.pk)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        campaign = get_object_or_404(MarketingCampaign, pk=pk)
        try:
            validate_campaign_deletable(campaign)
        except CampaignValidationError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:campaign_detail', pk=pk)
        if request.POST.get('confirm') != 'yes':
            messages.error(request, 'Подтвердите удаление кампании.')
            return redirect('marketing:campaign_delete', pk=pk)
        name = campaign.name
        campaign.delete()
        messages.success(request, f'Кампания «{name}» удалена.')
        return redirect('marketing:campaigns')
