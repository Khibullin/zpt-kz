from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, QuerySet

from marketing.services.campaigns.constants import PURPOSE_TEST_CAMPAIGN
from marketing.services.templates.constants import META_STATUS_APPROVED, USABLE_META_STATUSES
from marketing.services.templates.validation import (
    TemplateValidationError,
    is_reserved_service_template_name,
)

if TYPE_CHECKING:
    from marketing.models import MarketingWhatsAppTemplate


def template_is_selectable(template: MarketingWhatsAppTemplate) -> bool:
    return template.is_active and template.meta_status in USABLE_META_STATUSES


def template_allows_purpose(template: MarketingWhatsAppTemplate, purpose: str) -> bool:
    if purpose == PURPOSE_TEST_CAMPAIGN:
        return template.allow_test_campaign
    return purpose in template.allowed_purposes


def template_is_compatible_with_campaign(
    template: MarketingWhatsAppTemplate,
    *,
    purpose: str,
) -> bool:
    if is_reserved_service_template_name(template.meta_template_name):
        return False
    return template_is_selectable(template) and template_allows_purpose(template, purpose)


def compatible_templates_for_purpose(purpose: str):
    from marketing.models import MarketingWhatsAppTemplate

    queryset = MarketingWhatsAppTemplate.objects.filter(
        is_active=True,
        meta_status=META_STATUS_APPROVED,
    ).order_by('name')
    if purpose == PURPOSE_TEST_CAMPAIGN:
        template_ids = [
            template.pk
            for template in queryset
            if template.allow_test_campaign
        ]
    else:
        template_ids = [
            template.pk
            for template in queryset
            if purpose in template.allowed_purposes
        ]
    return MarketingWhatsAppTemplate.objects.filter(pk__in=template_ids).order_by('name')


def resolve_template_from_post(template_id: str, *, purpose: str):
    from marketing.models import MarketingWhatsAppTemplate

    cleaned = (template_id or '').strip()
    if not cleaned:
        return None
    try:
        template = MarketingWhatsAppTemplate.objects.get(pk=int(cleaned))
    except (MarketingWhatsAppTemplate.DoesNotExist, ValueError, TypeError) as exc:
        raise TemplateValidationError('Выбранный шаблон не найден.') from exc
    if not template_is_compatible_with_campaign(template, purpose=purpose):
        raise TemplateValidationError(
            'Выбранный шаблон недоступен для назначения кампании.',
        )
    return template


def template_list_queryset() -> QuerySet:
    from marketing.models import MarketingWhatsAppTemplate

    return (
        MarketingWhatsAppTemplate.objects.select_related('created_by')
        .annotate(campaign_count=Count('campaigns'))
        .order_by('-updated_at', '-id')
    )


def filter_template_list(queryset: QuerySet, params) -> QuerySet:
    meta_status = (params.get('meta_status') or '').strip()
    if meta_status:
        queryset = queryset.filter(meta_status=meta_status)

    language_code = (params.get('language_code') or '').strip()
    if language_code:
        queryset = queryset.filter(language_code=language_code)

    purpose = (params.get('purpose') or '').strip()
    if purpose:
        template_ids = [
            template.pk
            for template in queryset
            if purpose in template.allowed_purposes
        ]
        queryset = queryset.filter(pk__in=template_ids)

    is_active = (params.get('is_active') or '').strip()
    if is_active == '1':
        queryset = queryset.filter(is_active=True)
    elif is_active == '0':
        queryset = queryset.filter(is_active=False)

    return queryset


def _build_copy_internal_name(source: MarketingWhatsAppTemplate) -> str:
    from marketing.models import MarketingWhatsAppTemplate

    base = f'Копия — {source.name}'
    if not MarketingWhatsAppTemplate.objects.filter(name=base).exists():
        return base
    counter = 2
    while MarketingWhatsAppTemplate.objects.filter(name=f'Копия {counter} — {source.name}').exists():
        counter += 1
    return f'Копия {counter} — {source.name}'


def _build_copy_meta_template_name(source: MarketingWhatsAppTemplate) -> str:
    from marketing.models import MarketingWhatsAppTemplate

    base = source.meta_template_name
    suffix = '_copy'
    candidate = f'{base}{suffix}'[:150]
    counter = 2
    while MarketingWhatsAppTemplate.objects.filter(
        meta_template_name=candidate,
        language_code=source.language_code,
    ).exists():
        suffix = f'_copy{counter}'
        candidate = f'{base}{suffix}'[:150]
        counter += 1
    return candidate


def copy_template(
    source: MarketingWhatsAppTemplate,
    *,
    created_by,
) -> MarketingWhatsAppTemplate:
    from marketing.models import MarketingWhatsAppTemplate

    return MarketingWhatsAppTemplate.objects.create(
        name=_build_copy_internal_name(source),
        meta_template_name=_build_copy_meta_template_name(source),
        language_code=source.language_code,
        category=source.category,
        meta_status=source.meta_status,
        is_active=False,
        allowed_purposes=list(source.allowed_purposes),
        allow_test_campaign=source.allow_test_campaign,
        header_text=source.header_text,
        body_text=source.body_text,
        footer_text=source.footer_text,
        buttons=list(source.buttons),
        variables=list(source.variables),
        internal_notes=source.internal_notes,
        meta_template_id=source.meta_template_id,
        created_by=created_by,
    )
