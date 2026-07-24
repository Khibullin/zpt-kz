from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.services.buyer_contact_utils import mask_phone
from marketing.services.audiences.builders import subtype_matches_group
from marketing.services.audiences.constants import (
    CONTACT_GROUPS,
    GROUP_SUBTYPE_MAP,
    SUBTYPE_PARTS_REQUESTS,
)
from marketing.services.campaigns.constants import (
    CAMPAIGN_CHANNEL_CHOICES,
    CAMPAIGN_PURPOSE_CHOICES,
    CAMPAIGN_STATUS_CHOICES,
    CHANNEL_WHATSAPP,
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_EXCLUDED,
    EXCLUSION_REASON_CHOICES,
    PURPOSE_TEST_CAMPAIGN,
    STATUS_ARCHIVED,
    STATUS_AUDIENCE_PREPARED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
)
from marketing.services.campaigns.send_constants import (
    MESSAGE_STATUS_CHOICES,
    MESSAGE_STATUS_PENDING,
    SEND_MODE_LIVE,
    SEND_MODE_TEST,
    SEND_RUN_STATUS_CHOICES,
    SEND_RUN_STATUS_PENDING,
    SEND_RUN_STATUS_QUEUED,
    SEND_RUN_STATUS_RUNNING,
)
from marketing.services.templates.constants import (
    CATEGORY_MARKETING,
    META_STATUS_CHOICES,
    META_STATUS_UNKNOWN,
    TEMPLATE_BUSINESS_PURPOSE_CHOICES,
    USABLE_META_STATUSES,
)
from marketing.services.templates.validation import (
    TemplateValidationError,
    is_reserved_service_template_name,
    validate_allowed_purposes,
    validate_buttons,
    validate_language_code,
    validate_meta_template_name,
    validate_variables,
)


class MarketingCabinetPermission(models.Model):
    """Модель-носитель permission для внутреннего кабинета маркетинга."""

    class Meta:
        verbose_name = 'Доступ к кабинету маркетинга'
        verbose_name_plural = 'Доступ к кабинету маркетинга'
        permissions = [
            (
                'access_marketing_cabinet',
                'Доступ к кабинету рассылок и уведомлений',
            ),
        ]


class MarketingAudience(models.Model):
    name = models.CharField(max_length=200, verbose_name='Название аудитории')
    description = models.TextField(blank=True, default='', verbose_name='Описание')
    contact_group = models.CharField(
        max_length=32,
        choices=CONTACT_GROUPS,
        verbose_name='Группа контактов',
    )
    contact_subtype = models.CharField(
        max_length=32,
        verbose_name='Подтип',
    )
    criteria = models.JSONField(default=dict, blank=True, verbose_name='Критерии')
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_audiences',
        verbose_name='Автор',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создана')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлена')
    last_calculated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последний расчёт',
    )
    last_matched_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Найдено при последнем расчёте',
    )
    last_eligible_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Допустимо при последнем расчёте',
    )

    class Meta:
        verbose_name = 'Аудитория маркетинга'
        verbose_name_plural = 'Аудитории маркетинга'
        ordering = ('-updated_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('name',),
                name='marketing_audience_unique_name',
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def contact_group_label(self) -> str:
        return dict(CONTACT_GROUPS).get(self.contact_group, self.contact_group)

    @property
    def contact_subtype_label(self) -> str:
        choices = GROUP_SUBTYPE_MAP.get(self.contact_group, ())
        return dict(choices).get(self.contact_subtype, self.contact_subtype)

    def default_subtype_for_group(self) -> str:
        choices = GROUP_SUBTYPE_MAP.get(self.contact_group, ())
        if choices:
            return choices[0][0]
        return SUBTYPE_PARTS_REQUESTS

    def clean(self) -> None:
        super().clean()
        if not self.name.strip():
            raise ValidationError({'name': 'Укажите название аудитории.'})
        if not subtype_matches_group(self.contact_group, self.contact_subtype):
            raise ValidationError({
                'contact_subtype': 'Подтип не соответствует выбранной группе контактов.',
            })
        from marketing.services.audiences.validation import (
            CriteriaValidationError,
            validate_and_normalize_criteria,
        )

        try:
            self.criteria = validate_and_normalize_criteria(
                self.criteria,
                contact_group=self.contact_group,
                contact_subtype=self.contact_subtype,
                reject_unknown=False,
            )
        except CriteriaValidationError as exc:
            raise ValidationError({'criteria': str(exc)}) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MarketingWhatsAppTemplate(models.Model):
    name = models.CharField(max_length=200, verbose_name='Внутреннее название')
    meta_template_name = models.CharField(max_length=150, verbose_name='Meta template name')
    language_code = models.CharField(max_length=20, default='ru', verbose_name='Код языка')
    category = models.CharField(
        max_length=32,
        default=CATEGORY_MARKETING,
        verbose_name='Категория',
    )
    meta_status = models.CharField(
        max_length=16,
        choices=META_STATUS_CHOICES,
        default=META_STATUS_UNKNOWN,
        verbose_name='Статус в Meta (локально)',
    )
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    allowed_purposes = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Разрешённые назначения кампаний',
    )
    allow_test_campaign = models.BooleanField(
        default=False,
        verbose_name='Разрешить тестовую кампанию',
    )
    header_text = models.TextField(blank=True, default='', verbose_name='Header')
    body_text = models.TextField(blank=True, default='', verbose_name='Body')
    footer_text = models.TextField(blank=True, default='', verbose_name='Footer')
    buttons = models.JSONField(default=list, blank=True, verbose_name='Кнопки')
    variables = models.JSONField(default=list, blank=True, verbose_name='Переменные')
    internal_notes = models.TextField(blank=True, default='', verbose_name='Внутренние заметки')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_whatsapp_templates',
        verbose_name='Автор',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлён')
    last_status_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Последняя проверка статуса',
    )
    meta_template_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Meta template ID',
    )

    class Meta:
        verbose_name = 'Маркетинговый WhatsApp-шаблон'
        verbose_name_plural = 'Маркетинговые WhatsApp-шаблоны'
        ordering = ('-updated_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('name',),
                name='marketing_whatsapp_template_unique_name',
            ),
            models.UniqueConstraint(
                fields=('meta_template_name', 'language_code'),
                name='marketing_whatsapp_template_unique_meta_lang',
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def meta_status_label(self) -> str:
        return dict(META_STATUS_CHOICES).get(self.meta_status, self.meta_status)

    @property
    def is_selectable_for_campaign(self) -> bool:
        return self.is_active and self.meta_status in USABLE_META_STATUSES

    @property
    def allowed_purposes_labels(self) -> list[str]:
        labels = dict(TEMPLATE_BUSINESS_PURPOSE_CHOICES)
        return [labels.get(code, code) for code in self.allowed_purposes]

    def allows_campaign_purpose(self, purpose: str) -> bool:
        if purpose == PURPOSE_TEST_CAMPAIGN:
            return self.allow_test_campaign
        return purpose in self.allowed_purposes

    def clean(self) -> None:
        super().clean()
        if not self.name.strip():
            raise ValidationError({'name': 'Укажите внутреннее название шаблона.'})
        self.category = CATEGORY_MARKETING
        try:
            self.meta_template_name = validate_meta_template_name(self.meta_template_name)
            self.language_code = validate_language_code(self.language_code)
            self.allowed_purposes = validate_allowed_purposes(list(self.allowed_purposes or []))
        except TemplateValidationError as exc:
            raise ValidationError(str(exc)) from exc
        if is_reserved_service_template_name(self.meta_template_name):
            raise ValidationError({
                'meta_template_name': 'Это имя зарезервировано для сервисных WhatsApp-шаблонов.',
            })
        try:
            self.variables = validate_variables(self.variables)
            self.buttons = validate_buttons(self.buttons)
        except TemplateValidationError as exc:
            raise ValidationError(str(exc)) from exc

    def save(self, *args, **kwargs):
        self.category = CATEGORY_MARKETING
        self.full_clean()
        super().save(*args, **kwargs)


class MarketingCampaign(models.Model):
    name = models.CharField(max_length=200, verbose_name='Название кампании')
    description = models.TextField(blank=True, default='', verbose_name='Описание')
    audience = models.ForeignKey(
        MarketingAudience,
        on_delete=models.PROTECT,
        related_name='campaigns',
        verbose_name='Аудитория',
    )
    purpose = models.CharField(
        max_length=32,
        choices=CAMPAIGN_PURPOSE_CHOICES,
        verbose_name='Назначение',
    )
    channel = models.CharField(
        max_length=16,
        choices=CAMPAIGN_CHANNEL_CHOICES,
        default=CHANNEL_WHATSAPP,
        verbose_name='Канал',
    )
    status = models.CharField(
        max_length=32,
        choices=CAMPAIGN_STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name='Статус',
    )
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_campaigns',
        verbose_name='Автор',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создана')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлена')
    audience_prepared_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Получатели подготовлены',
    )
    audience_updated_at_at_prepare = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Версия аудитории при подготовке',
    )
    audience_signature_at_prepare = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='Сигнатура аудитории при подготовке',
    )
    matched_count = models.PositiveIntegerField(default=0, verbose_name='Найдено')
    unique_count = models.PositiveIntegerField(default=0, verbose_name='Уникальных')
    eligible_count = models.PositiveIntegerField(default=0, verbose_name='Допустимо')
    excluded_count = models.PositiveIntegerField(default=0, verbose_name='Исключено')
    test_count = models.PositiveIntegerField(default=0, verbose_name='Тестовых')
    invalid_phone_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Некорректных телефонов',
    )
    duplicate_count = models.PositiveIntegerField(default=0, verbose_name='Дубликатов')
    inactive_count = models.PositiveIntegerField(default=0, verbose_name='Неактивных')
    consent_granted_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Согласие дано',
    )
    consent_unknown_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Согласие не подтверждено',
    )
    consent_revoked_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Согласие отозвано',
    )
    consent_not_recorded_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Согласие не зафиксировано',
    )
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name='Архивирована')
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name='Отменена')
    message_template = models.ForeignKey(
        MarketingWhatsAppTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='campaigns',
        verbose_name='WhatsApp-шаблон',
    )
    template_selected_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Шаблон выбран',
    )

    class Meta:
        verbose_name = 'Маркетинговая кампания'
        verbose_name_plural = 'Маркетинговые кампании'
        ordering = ('-created_at', '-id')

    def __str__(self) -> str:
        return self.name

    @property
    def purpose_label(self) -> str:
        return dict(CAMPAIGN_PURPOSE_CHOICES).get(self.purpose, self.purpose)

    @property
    def status_label(self) -> str:
        return dict(CAMPAIGN_STATUS_CHOICES).get(self.display_status, self.display_status)

    @property
    def has_prepared_snapshot(self) -> bool:
        return self.audience_prepared_at is not None

    def is_snapshot_stale(self) -> bool:
        if not self.audience_prepared_at or not self.audience_signature_at_prepare:
            return False
        from marketing.services.campaigns.signatures import compute_audience_signature

        return compute_audience_signature(self.audience) != self.audience_signature_at_prepare

    @property
    def display_status(self) -> str:
        if self.status == STATUS_AUDIENCE_PREPARED and self.is_snapshot_stale():
            from marketing.services.campaigns.constants import STATUS_AUDIENCE_STALE

            return STATUS_AUDIENCE_STALE
        return self.status

    @property
    def is_editable(self) -> bool:
        if self.status in {STATUS_CANCELLED, STATUS_ARCHIVED}:
            return False
        return self.status in {STATUS_DRAFT, STATUS_AUDIENCE_PREPARED} or self.is_snapshot_stale()

    @property
    def is_ready_for_send(self) -> bool:
        return (
            self.status == STATUS_AUDIENCE_PREPARED
            and self.has_prepared_snapshot
            and not self.is_snapshot_stale()
        )

    @property
    def message_template_allows_purpose(self) -> bool:
        if not self.message_template_id:
            return False
        return self.message_template.allows_campaign_purpose(self.purpose)

    def clean(self) -> None:
        super().clean()
        from marketing.services.campaigns.validation import campaign_model_clean

        campaign_model_clean(self)
        if self.message_template_id and self.purpose:
            template = self.message_template
            if not template.is_selectable_for_campaign:
                raise ValidationError({
                    'message_template': 'Выбранный шаблон недоступен для использования.',
                })
            if not template.allows_campaign_purpose(self.purpose):
                raise ValidationError({
                    'message_template': 'Шаблон несовместим с назначением кампании.',
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MarketingCampaignRecipient(models.Model):
    campaign = models.ForeignKey(
        MarketingCampaign,
        on_delete=models.CASCADE,
        related_name='recipients',
        verbose_name='Кампания',
    )
    phone_normalized = models.CharField(max_length=32, verbose_name='Телефон (внутренний)')
    display_name = models.CharField(max_length=255, default='—', verbose_name='Имя')
    city = models.CharField(max_length=255, default='—', verbose_name='Город')
    roles = models.JSONField(default=list, verbose_name='Роли')
    vehicle_summary = models.CharField(max_length=255, default='—', verbose_name='Автомобиль')
    last_activity_at = models.DateTimeField(null=True, blank=True, verbose_name='Активность')
    is_test_contact = models.BooleanField(default=False, verbose_name='Тестовый контакт')
    consent_status = models.CharField(max_length=32, default='', verbose_name='Согласие')
    eligibility_status = models.CharField(
        max_length=16,
        choices=(
            (ELIGIBILITY_ELIGIBLE, 'Допустим'),
            (ELIGIBILITY_EXCLUDED, 'Исключён'),
        ),
        verbose_name='Статус',
    )
    exclusion_reason = models.CharField(
        max_length=32,
        choices=EXCLUSION_REASON_CHOICES,
        blank=True,
        default='',
        verbose_name='Причина исключения',
    )
    source_summary = models.JSONField(default=dict, verbose_name='Источник')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')

    class Meta:
        verbose_name = 'Получатель кампании'
        verbose_name_plural = 'Получатели кампании'
        ordering = ('-last_activity_at', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=('campaign', 'phone_normalized'),
                name='marketing_campaign_recipient_unique_phone',
            ),
        ]

    def __str__(self) -> str:
        return self.masked_phone

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.phone_normalized)

    @property
    def roles_display(self) -> str:
        if not self.roles:
            return '—'
        return ', '.join(self.roles)

    @property
    def consent_status_label(self) -> str:
        from core.models import (
            CONTACT_CONSENT_STATUS_GRANTED,
            CONTACT_CONSENT_STATUS_REVOKED,
            CONTACT_CONSENT_STATUS_UNKNOWN,
        )

        labels = {
            CONTACT_CONSENT_STATUS_GRANTED: 'Дано',
            CONTACT_CONSENT_STATUS_REVOKED: 'Отозвано',
            CONTACT_CONSENT_STATUS_UNKNOWN: 'Не подтверждено',
            '': 'Не зафиксировано',
        }
        return labels.get(self.consent_status, self.consent_status)

    @property
    def exclusion_reason_label(self) -> str:
        from marketing.services.campaigns.summaries import exclusion_reason_label

        return exclusion_reason_label(self.exclusion_reason)


class MarketingCampaignSendRun(models.Model):
    campaign = models.ForeignKey(
        MarketingCampaign,
        on_delete=models.PROTECT,
        related_name='send_runs',
        verbose_name='Кампания',
    )
    template = models.ForeignKey(
        MarketingWhatsAppTemplate,
        on_delete=models.PROTECT,
        related_name='send_runs',
        verbose_name='Шаблон',
    )
    mode = models.CharField(
        max_length=16,
        default=SEND_MODE_TEST,
        verbose_name='Режим',
    )
    status = models.CharField(
        max_length=32,
        choices=SEND_RUN_STATUS_CHOICES,
        default=SEND_RUN_STATUS_PENDING,
        verbose_name='Статус',
    )
    total_count = models.PositiveIntegerField(default=0, verbose_name='Всего')
    queued_count = models.PositiveIntegerField(default=0, verbose_name='В очереди')
    sent_count = models.PositiveIntegerField(default=0, verbose_name='Отправлено')
    failed_count = models.PositiveIntegerField(default=0, verbose_name='Ошибок')
    skipped_count = models.PositiveIntegerField(default=0, verbose_name='Пропущено')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_campaign_send_runs',
        verbose_name='Инициатор',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Начат')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='Завершён')

    class Meta:
        verbose_name = 'Запуск отправки кампании'
        verbose_name_plural = 'Запуски отправки кампаний'
        ordering = ('-created_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('campaign',),
                condition=models.Q(mode=SEND_MODE_TEST, status=SEND_RUN_STATUS_RUNNING),
                name='marketing_campaign_one_running_test_send',
            ),
            models.UniqueConstraint(
                fields=('campaign',),
                condition=(
                    models.Q(mode=SEND_MODE_LIVE)
                    & models.Q(
                        status__in=(
                            SEND_RUN_STATUS_QUEUED,
                            SEND_RUN_STATUS_RUNNING,
                        ),
                    )
                ),
                name='uniq_active_live_run_per_campaign',
            ),
        ]
        indexes = [
            models.Index(fields=['mode', 'status', '-created_at'], name='marketing_sendrun_mode_status'),
        ]

    def __str__(self) -> str:
        return f'SendRun #{self.pk} campaign={self.campaign_id} ({self.mode})'


class MarketingCampaignMessage(models.Model):
    send_run = models.ForeignKey(
        MarketingCampaignSendRun,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='Запуск',
    )
    campaign_recipient = models.ForeignKey(
        MarketingCampaignRecipient,
        on_delete=models.PROTECT,
        related_name='send_messages',
        verbose_name='Получатель снимка',
    )
    phone_normalized = models.CharField(max_length=32, verbose_name='Телефон (snapshot)')
    template_name = models.CharField(max_length=150, verbose_name='Meta template name')
    language_code = models.CharField(max_length=20, verbose_name='Язык')
    variables = models.JSONField(default=dict, blank=True, verbose_name='Переменные')
    status = models.CharField(
        max_length=16,
        choices=MESSAGE_STATUS_CHOICES,
        default=MESSAGE_STATUS_PENDING,
        verbose_name='Статус',
    )
    meta_message_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        verbose_name='Meta message ID',
    )
    error_code = models.CharField(max_length=64, blank=True, default='', verbose_name='Код ошибки')
    error_message = models.TextField(blank=True, default='', verbose_name='Ошибка')
    attempted_at = models.DateTimeField(null=True, blank=True, verbose_name='Попытка')
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name='Отправлено')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создан')

    class Meta:
        verbose_name = 'Сообщение кампании'
        verbose_name_plural = 'Сообщения кампаний'
        ordering = ('id',)
        constraints = [
            models.UniqueConstraint(
                fields=('send_run', 'campaign_recipient'),
                name='marketing_campaign_message_unique_recipient_run',
            ),
        ]
        indexes = [
            models.Index(fields=['send_run', 'status'], name='marketing_msg_run_status'),
        ]

    def __str__(self) -> str:
        return f'Message #{self.pk} run={self.send_run_id} ({self.status})'

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.phone_normalized)
