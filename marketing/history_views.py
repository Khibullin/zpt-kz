from __future__ import annotations

from django.core.paginator import Paginator
from django.views.generic import TemplateView

from marketing.models import MarketingCampaignMessage, MarketingCampaignSendRun
from marketing.views import MarketingCabinetMixin

HISTORY_PAGE_SIZE = 25


class MarketingHistoryView(MarketingCabinetMixin, TemplateView):
    template_name = 'marketing/history.html'
    active_nav = 'history'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.get_broadcast_mode_context())
        context.update(self.get_marketing_send_mode_context())
        context.update(self.get_nav_context())

        runs = (
            MarketingCampaignSendRun.objects.select_related(
                'campaign',
                'template',
                'created_by',
            )
            .order_by('-created_at', '-id')
        )
        paginator = Paginator(runs, HISTORY_PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page'))
        run_ids = [run.pk for run in page_obj.object_list]
        messages_by_run: dict[int, list[MarketingCampaignMessage]] = {}
        if run_ids:
            for message in (
                MarketingCampaignMessage.objects.filter(send_run_id__in=run_ids)
                .select_related('campaign_recipient')
                .order_by('id')
            ):
                messages_by_run.setdefault(message.send_run_id, []).append(message)

        run_entries = [
            (run, messages_by_run.get(run.pk, []))
            for run in page_obj.object_list
        ]

        context['page_obj'] = page_obj
        context['run_entries'] = run_entries
        query_params = self.request.GET.copy()
        query_params.pop('page', None)
        context['pagination_querystring'] = query_params.urlencode()
        return context
