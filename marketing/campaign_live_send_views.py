from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from marketing.models import MarketingCampaign
from marketing.services.campaigns.live_send import create_live_send_queue
from marketing.services.campaigns.live_send_validation import (
    LiveSendValidationError,
    build_live_send_preflight,
)
from marketing.services.campaigns.live_processor import cancel_live_send_run
from marketing.services.campaigns.send_settings import (
    get_marketing_live_batch_size,
    get_marketing_live_max_recipients,
    get_marketing_whatsapp_send_mode,
)
from marketing.views import MarketingCabinetMixin

logger = logging.getLogger(__name__)


class CampaignLiveSendPreflightView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/live_send_preflight.html'
    active_nav = 'campaigns'

    def get_campaign(self) -> MarketingCampaign:
        return get_object_or_404(
            MarketingCampaign.objects.select_related('message_template', 'audience'),
            pk=self.kwargs['pk'],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_marketing_send_mode_context())
        context.update(self.get_nav_context())
        campaign = self.get_campaign()
        context['campaign'] = campaign
        context['preflight'] = build_live_send_preflight(campaign)
        return context

    def post(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET'])


class CampaignLiveSendConfirmView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/live_send_confirm.html'
    active_nav = 'campaigns'

    def get_campaign(self) -> MarketingCampaign:
        return get_object_or_404(
            MarketingCampaign.objects.select_related('message_template', 'audience'),
            pk=self.kwargs['pk'],
        )

    def dispatch(self, request, *args, **kwargs):
        campaign = get_object_or_404(MarketingCampaign, pk=kwargs['pk'])
        preflight = build_live_send_preflight(campaign)
        if not preflight.allowed:
            messages.error(
                request,
                preflight.blocking_errors[0] if preflight.blocking_errors else 'LIVE-отправка недоступна.',
            )
            return redirect('marketing:campaign_live_send_preflight', pk=campaign.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_marketing_send_mode_context())
        context.update(self.get_nav_context())
        campaign = self.get_campaign()
        preflight = build_live_send_preflight(campaign)
        context['campaign'] = campaign
        context['preflight'] = preflight
        context['batch_limit'] = get_marketing_live_batch_size()
        context['max_recipients'] = get_marketing_live_max_recipients()
        return context

    def post(self, request, pk):
        campaign = self.get_campaign()
        confirmation_text = request.POST.get('confirmation_text', '')
        try:
            result = create_live_send_queue(
                campaign.pk,
                created_by=request.user,
                confirmation_text=confirmation_text,
            )
        except LiveSendValidationError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:campaign_live_send_confirm', pk=campaign.pk)
        except Exception as exc:
            logger.exception(
                'Marketing LIVE queue unexpected error for campaign #%s (mode=%s): %s',
                campaign.pk,
                get_marketing_whatsapp_send_mode(),
                exc.__class__.__name__,
            )
            messages.error(
                request,
                'Не удалось создать LIVE-очередь. Подробности записаны в журнал сервера.',
            )
            return redirect('marketing:campaign_live_send_confirm', pk=campaign.pk)

        messages.success(
            request,
            (
                f'LIVE-очередь создана (run #{result.send_run_id}): '
                f'{result.queued_count} сообщений в очереди.'
            ),
        )
        return redirect('marketing:history')

    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CampaignLiveSendCancelView(MarketingCabinetMixin, View):
    def post(self, request, pk, run_id):
        try:
            cancel_live_send_run(run_id)
        except Exception as exc:
            logger.exception(
                'Marketing LIVE cancel error for run #%s campaign #%s: %s',
                run_id,
                pk,
                exc.__class__.__name__,
            )
            messages.error(request, 'Не удалось отменить LIVE-отправку.')
        else:
            messages.success(request, 'LIVE-отправка отменена.')
        return redirect('marketing:campaign_detail', pk=pk)

    def get(self, request, pk, run_id):
        return HttpResponseNotAllowed(['POST'])
