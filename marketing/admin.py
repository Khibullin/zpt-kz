from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Count, Max, Min, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from core.whatsapp_redaction import redact_whatsapp_sensitive_data
from core.whatsapp_template_management import (
    APPROVED_MESSAGE,
    submit_whatsapp_template,
    sync_whatsapp_template_status,
)
from marketing.models import (
    MarketingCabinetPermission,
    MarketingCampaignMessage,
    MarketingCampaignSendRun,
    MarketingWhatsAppTemplate,
)
from marketing.services.templates.constants import META_STATUS_APPROVED
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_QUEUED,
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS,
    RECIPIENT_SCOPE_CONTROL_ONLY,
    WORKFLOW_TYPE_CHOICES,
    WORKFLOW_TYPE_SIMPLE_MAILING,
)

admin.site.register(MarketingCabinetPermission)

RECIPIENT_SCOPE_ADMIN_LABELS = {
    RECIPIENT_SCOPE_CONTROL_ONLY: 'Контрольная',
    RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS: 'Рабочая + CONTROL',
    '': 'Legacy / не зафиксировано',
}

RECIPIENT_SCOPE_LEGACY = '__legacy__'
RECENT_SEND_RUN_FILTER_LIMIT = 50


def recipient_scope_admin_label(value: str | None) -> str:
    if not value:
        return RECIPIENT_SCOPE_ADMIN_LABELS['']
    return RECIPIENT_SCOPE_ADMIN_LABELS.get(value, value)


def _truncate(text: str, *, limit: int = 80) -> str:
    if not text:
        return '—'
    if len(text) <= limit:
        return text
    return f'{text[:limit]}…'


def _safe_error_text(text: str) -> str:
    sanitized = redact_whatsapp_sensitive_data(str(text or ''))
    if isinstance(sanitized, str):
        return sanitized
    return str(sanitized)


def _campaign_display_datetime(send_run, campaign):
    dt = send_run.started_at or send_run.created_at or campaign.created_at
    return timezone.localtime(dt)


class RecipientScopeFilter(admin.SimpleListFilter):
    title = 'Область получателей'
    parameter_name = 'recipient_scope'

    def lookups(self, request, model_admin):
        return [
            (RECIPIENT_SCOPE_CONTROL_ONLY, 'Контрольная'),
            (RECIPIENT_SCOPE_AUDIENCE_PLUS_CONTROLS, 'Рабочая + CONTROL'),
            (RECIPIENT_SCOPE_LEGACY, 'Legacy / не зафиксировано'),
        ]

    def queryset(self, request, queryset):
        if self.value() == RECIPIENT_SCOPE_LEGACY:
            return queryset.filter(recipient_scope='')
        if self.value():
            return queryset.filter(recipient_scope=self.value())
        return queryset


class MessageRecipientScopeFilter(admin.SimpleListFilter):
    title = 'Область получателей'
    parameter_name = 'recipient_scope'

    def lookups(self, request, model_admin):
        return RecipientScopeFilter.lookups(self, request, model_admin)

    def queryset(self, request, queryset):
        if self.value() == RECIPIENT_SCOPE_LEGACY:
            return queryset.filter(send_run__recipient_scope='')
        if self.value():
            return queryset.filter(send_run__recipient_scope=self.value())
        return queryset


class MessageWorkflowTypeFilter(admin.SimpleListFilter):
    title = 'Тип workflow'
    parameter_name = 'workflow_type'

    def lookups(self, request, model_admin):
        return WORKFLOW_TYPE_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(send_run__workflow_type=self.value())
        return queryset


class ControlRecipientFilter(admin.SimpleListFilter):
    title = 'CONTROL'
    parameter_name = 'control'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Да'),
            ('no', 'Нет'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(campaign_recipient__is_control_recipient=True)
        if self.value() == 'no':
            return queryset.filter(campaign_recipient__is_control_recipient=False)
        return queryset


class RecentSendRunFilter(admin.SimpleListFilter):
    title = 'Запуск'
    parameter_name = 'send_run'

    def lookups(self, request, model_admin):
        runs = (
            MarketingCampaignSendRun.objects.select_related('campaign')
            .order_by('-id')[:RECENT_SEND_RUN_FILTER_LIMIT]
        )
        return [
            (str(run.pk), f'#{run.pk} — {run.campaign.name[:40]}')
            for run in runs
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(send_run_id=self.value())
        return queryset


@admin.register(MarketingWhatsAppTemplate)
class MarketingWhatsAppTemplateAdmin(admin.ModelAdmin):
    change_form_template = 'admin/marketing/marketingwhatsapptemplate/change_form.html'
    list_display = (
        'id',
        'name',
        'meta_template_name',
        'language_code',
        'meta_status',
        'is_active',
        'allowed_purposes_short',
        'updated_at',
    )
    list_filter = (
        'meta_status',
        'is_active',
        'language_code',
    )
    search_fields = (
        'name',
        'meta_template_name',
    )
    ordering = ('-updated_at', '-id')
    readonly_fields = (
        'created_at',
        'updated_at',
        'last_status_checked_at',
        'meta_template_id',
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/submit-to-meta/',
                self.admin_site.admin_view(self.submit_to_meta_view),
                name='marketing_marketingwhatsapptemplate_submit_to_meta',
            ),
            path(
                '<path:object_id>/check-meta-status/',
                self.admin_site.admin_view(self.check_meta_status_view),
                name='marketing_marketingwhatsapptemplate_check_meta_status',
            ),
        ]
        return custom + urls

    def _can_manage_meta_templates(self, request):
        user = getattr(request, 'user', None)
        return bool(
            user
            and user.is_active
            and user.is_staff
            and (
                user.is_superuser
                or user.has_perm('marketing.change_marketingwhatsapptemplate')
            )
        )

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(self._meta_action_context(object_id))
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context
        )

    def _meta_action_context(self, object_id):
        template = MarketingWhatsAppTemplate.objects.filter(pk=object_id).first()
        return {
            'meta_submit_url': reverse(
                'admin:marketing_marketingwhatsapptemplate_submit_to_meta',
                args=[object_id],
            ),
            'meta_status_url': reverse(
                'admin:marketing_marketingwhatsapptemplate_check_meta_status',
                args=[object_id],
            ),
            'show_meta_submit': bool(
                template is not None and template.meta_status != META_STATUS_APPROVED
            ),
        }

    def _change_redirect(self, object_id):
        return redirect(
            reverse(
                'admin:marketing_marketingwhatsapptemplate_change',
                args=[object_id],
            )
        )

    def _apply_management_message(self, request, result):
        if result.waba_missing or result.config_error:
            messages.error(request, result.message)
            return
        if result.conflict or not result.ok:
            messages.error(request, result.message)
            return
        if result.meta_status == META_STATUS_APPROVED:
            messages.success(request, result.message or APPROVED_MESSAGE)
            return
        if result.already_exists or result.created or result.ok:
            messages.success(request, result.message)

    def submit_to_meta_view(self, request, object_id):
        if not self._can_manage_meta_templates(request):
            return HttpResponseForbidden('Недостаточно прав.')
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=object_id)
        if template.meta_status == META_STATUS_APPROVED:
            messages.info(request, 'Шаблон уже одобрен Meta.')
            return self._change_redirect(object_id)

        if request.method == 'POST' and request.POST.get('confirm') == '1':
            result = submit_whatsapp_template(template)
            self._apply_management_message(request, result)
            return self._change_redirect(object_id)

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': template,
            'title': 'Отправка шаблона в Meta',
            'header_text': template.header_text,
            'body_text': template.body_text,
            'footer_text': template.footer_text,
            'buttons': template.buttons or [],
        }
        return render(
            request,
            'admin/marketing/marketingwhatsapptemplate/submit_confirm.html',
            context,
        )

    def check_meta_status_view(self, request, object_id):
        if not self._can_manage_meta_templates(request):
            return HttpResponseForbidden('Недостаточно прав.')
        if request.method != 'POST':
            return self._change_redirect(object_id)
        template = get_object_or_404(MarketingWhatsAppTemplate, pk=object_id)
        result = sync_whatsapp_template_status(template)
        self._apply_management_message(request, result)
        return self._change_redirect(object_id)

    @admin.display(description='Назначения')
    def allowed_purposes_short(self, obj: MarketingWhatsAppTemplate) -> str:
        purposes = list(obj.allowed_purposes or [])
        if not purposes:
            return '—'
        labels = obj.allowed_purposes_labels
        text = ', '.join(labels[:3])
        if len(labels) > 3:
            text += f' (+{len(labels) - 3})'
        return text


class ReadOnlyMarketingSendRunAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


@admin.register(MarketingCampaignSendRun)
class MarketingCampaignSendRunAdmin(ReadOnlyMarketingSendRunAdmin):
    list_display = (
        'id',
        'created_at',
        'campaign',
        'workflow_type',
        'recipient_scope_display',
        'mode',
        'status',
        'total_count',
        'queued_count',
        'sent_count',
        'failed_count',
        'skipped_count',
        'total_waves_display',
        'current_wave_display',
        'messages_link',
        'created_by',
        'finished_at',
    )
    list_filter = (
        'workflow_type',
        RecipientScopeFilter,
        'mode',
        'status',
        ('created_at', admin.DateFieldListFilter),
    )
    search_fields = (
        'campaign__name',
        'template__name',
        'template__meta_template_name',
        'created_by__username',
        'created_by__email',
    )
    ordering = ('-created_at', '-id')
    list_select_related = (
        'campaign',
        'template',
        'created_by',
    )
    readonly_fields = (
        'campaign',
        'template',
        'mode',
        'status',
        'workflow_type',
        'recipient_scope',
        'recipient_scope_display',
        'total_count',
        'queued_count',
        'sent_count',
        'failed_count',
        'skipped_count',
        'simple_mailing_key',
        'active_simple_mailing_lock',
        'created_by',
        'created_at',
        'started_at',
        'finished_at',
        'total_waves_display',
        'current_wave_display',
        'messages_link',
    )
    fields = readonly_fields

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            annotated_total_waves=Max('messages__wave_number'),
            annotated_current_wave=Min(
                'messages__wave_number',
                filter=Q(messages__status=MESSAGE_STATUS_QUEUED),
            ),
            annotated_messages_count=Count('messages'),
        )

    @admin.display(description='Область получателей', ordering='recipient_scope')
    def recipient_scope_display(self, obj: MarketingCampaignSendRun) -> str:
        return recipient_scope_admin_label(obj.recipient_scope)

    @admin.display(description='Волн', ordering='annotated_total_waves')
    def total_waves_display(self, obj: MarketingCampaignSendRun) -> str:
        total = getattr(obj, 'annotated_total_waves', None)
        if total is None:
            return '—'
        return str(total)

    @admin.display(description='Текущая волна')
    def current_wave_display(self, obj: MarketingCampaignSendRun) -> str:
        current = getattr(obj, 'annotated_current_wave', None)
        total = getattr(obj, 'annotated_total_waves', None)
        if current is None:
            if total:
                return f'завершено ({total})'
            return '—'
        return str(current)

    @admin.display(description='Сообщения')
    def messages_link(self, obj: MarketingCampaignSendRun) -> str:
        count = getattr(obj, 'annotated_messages_count', None)
        if count is None:
            count = obj.messages.count()
        url = (
            reverse('admin:marketing_marketingcampaignmessage_changelist')
            + f'?send_run={obj.pk}'
        )
        return format_html('<a href="{}">{} сообщений</a>', url, count)


class ReadOnlyMarketingMessageAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


@admin.register(MarketingCampaignMessage)
class MarketingCampaignMessageAdmin(ReadOnlyMarketingMessageAdmin):
    list_display = (
        'id',
        'send_run_link',
        'campaign_short_display',
        'masked_phone_display',
        'control_display',
        'recipient_scope_display',
        'wave_number',
        'position_number',
        'status',
        'meta_message_id_short',
        'error_code',
        'error_message_short',
        'attempted_at',
        'sent_at',
    )
    list_filter = (
        'status',
        MessageWorkflowTypeFilter,
        MessageRecipientScopeFilter,
        ControlRecipientFilter,
        'wave_number',
        ('sent_at', admin.DateFieldListFilter),
        ('created_at', admin.DateFieldListFilter),
        RecentSendRunFilter,
    )
    search_fields = (
        'phone_normalized',
        'meta_message_id',
        'error_code',
        'send_run__campaign__name',
        'send_run__template__name',
        'send_run__template__meta_template_name',
    )
    ordering = ('-id',)
    list_select_related = (
        'send_run',
        'send_run__campaign',
        'send_run__template',
        'campaign_recipient',
    )
    readonly_fields = (
        'send_run',
        'send_run_link',
        'campaign_recipient',
        'campaign_name_full',
        'phone_normalized',
        'masked_phone_display',
        'control_display',
        'recipient_scope_display',
        'template_name',
        'language_code',
        'variables',
        'status',
        'meta_message_id',
        'meta_message_id_short',
        'error_code',
        'safe_error_message_display',
        'attempted_at',
        'sent_at',
        'created_at',
        'wave_number',
        'position_number',
        'scheduled_at',
    )
    fields = readonly_fields

    @admin.display(description='Запуск', ordering='send_run_id')
    def send_run_link(self, obj: MarketingCampaignMessage) -> str:
        url = reverse(
            'admin:marketing_marketingcampaignsendrun_change',
            args=[obj.send_run_id],
        )
        return format_html('<a href="{}">#{}</a>', url, obj.send_run_id)

    @admin.display(description='Кампания', ordering='send_run__campaign_id')
    def campaign_short_display(self, obj: MarketingCampaignMessage) -> str:
        campaign = obj.send_run.campaign
        send_run = obj.send_run
        date_label = _campaign_display_datetime(send_run, campaign).strftime('%d.%m.%Y')
        label = f'Кампания №{campaign.pk} — {date_label}'
        url = reverse('marketing:campaign_detail', args=[campaign.pk])
        return format_html(
            '<a href="{}" title="{}">{}</a>',
            url,
            campaign.name,
            label,
        )

    @admin.display(description='Кампания (полное название)')
    def campaign_name_full(self, obj: MarketingCampaignMessage) -> str:
        return obj.send_run.campaign.name

    @admin.display(description='Телефон')
    def masked_phone_display(self, obj: MarketingCampaignMessage) -> str:
        return obj.masked_phone

    @admin.display(description='CONTROL', ordering='campaign_recipient__is_control_recipient')
    def control_display(self, obj: MarketingCampaignMessage) -> str:
        if obj.campaign_recipient.is_control_recipient:
            return 'CONTROL'
        return '—'

    @admin.display(description='Область получателей', ordering='send_run__recipient_scope')
    def recipient_scope_display(self, obj: MarketingCampaignMessage) -> str:
        return recipient_scope_admin_label(obj.send_run.recipient_scope)

    @admin.display(description='Meta message ID')
    def meta_message_id_short(self, obj: MarketingCampaignMessage) -> str:
        return _truncate(obj.meta_message_id, limit=24)

    @admin.display(description='Ошибка')
    def error_message_short(self, obj: MarketingCampaignMessage) -> str:
        return _truncate(_safe_error_text(obj.error_message), limit=80)

    @admin.display(description='Ошибка (безопасно)')
    def safe_error_message_display(self, obj: MarketingCampaignMessage) -> str:
        safe_text = _safe_error_text(obj.error_message)
        return safe_text or '—'
