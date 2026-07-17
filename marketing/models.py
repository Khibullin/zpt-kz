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
    STATUS_ARCHIVED,
    STATUS_AUDIENCE_PREPARED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
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

    def clean(self) -> None:
        super().clean()
        from marketing.services.campaigns.validation import campaign_model_clean

        campaign_model_clean(self)

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
