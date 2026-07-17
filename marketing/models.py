from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from marketing.services.audiences.builders import subtype_matches_group
from marketing.services.audiences.constants import (
    CONTACT_GROUPS,
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    GROUP_SUBTYPE_MAP,
    GROUP_TEST,
    SUBTYPE_PARTS_REQUESTS,
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
