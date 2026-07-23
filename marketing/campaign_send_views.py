from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from marketing.models import MarketingCampaign
from marketing.services.campaigns.send_validation import (
    TestSendValidationError,
    build_test_send_preflight,
)
from marketing.services.campaigns.test_send import execute_test_campaign_send
from marketing.views import MarketingCabinetMixin


class CampaignTestSendPreflightView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/campaigns/test_send_preflight.html'
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
        context['preflight'] = build_test_send_preflight(campaign)
        return context

    def post(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET'])


class CampaignTestSendExecuteView(MarketingCabinetMixin, View):
    def post(self, request, pk):
        campaign = get_object_or_404(
            MarketingCampaign.objects.select_related('message_template'),
            pk=pk,
        )
        try:
            result = execute_test_campaign_send(
                campaign.pk,
                created_by=request.user,
            )
        except TestSendValidationError as exc:
            messages.error(request, str(exc))
            return redirect('marketing:campaign_test_send_preflight', pk=campaign.pk)

        if result.sent_count and result.failed_count:
            messages.warning(
                request,
                (
                    f'Тестовая отправка завершена: отправлено {result.sent_count}, '
                    f'ошибок {result.failed_count}.'
                ),
            )
        elif result.sent_count:
            messages.success(
                request,
                f'Тестовая отправка завершена: отправлено {result.sent_count} сообщений.',
            )
        else:
            messages.error(
                request,
                f'Тестовая отправка не удалась: ошибок {result.failed_count}, '
                f'пропущено {result.skipped_count}.',
            )
        return redirect('marketing:history')

    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['POST'])
