from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from core.buyer_contact_admin_filters import marketing_consent_label
from core.services.buyer_contact_utils import mask_phone
from core.models import (
    BUYER_BROADCAST_MODE_TEST,
    BuyerBroadcastCampaign,
    BuyerContact,
    ContactConsent,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
)
from core.services.buyer_broadcast_settings import get_buyer_broadcast_test_max_recipients


def _marketing_consent_status_for_buyer(buyer: BuyerContact) -> str:
    consent = ContactConsent.objects.filter(
        buyer=buyer,
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
    ).order_by('-updated_at', '-id').first()
    return marketing_consent_label(consent.status if consent else None)


class BuyerBroadcastCampaignAdminForm(forms.ModelForm):
    class Meta:
        model = BuyerBroadcastCampaign
        fields = (
            'name',
            'description',
            'mode',
            'status',
            'template_name',
            'template_language',
            'template_body_parameters',
            'message_preview',
            'test_contacts',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = BuyerContact.objects.filter(is_test_contact=True).order_by(
            '-last_request_at',
            '-id',
        )
        self.fields['test_contacts'].queryset = queryset
        self.fields['test_contacts'].label_from_instance = self._contact_label
        self.fields['mode'].disabled = True
        self.fields['status'].choices = [
            choice
            for choice in self.fields['status'].choices
            if choice[0] in {'draft', 'ready', 'cancelled'}
            or (
                self.instance.pk
                and choice[0] == self.instance.status
            )
        ]

    @staticmethod
    def _contact_label(buyer: BuyerContact) -> str:
        consent_status = _marketing_consent_status_for_buyer(buyer)
        city = buyer.primary_city or '—'
        return (
            f'{mask_phone(buyer.phone_normalized)} | {city} | '
            f'заявок: {buyer.requests_count} | marketing: {consent_status}'
        )

    def clean_mode(self):
        mode = self.cleaned_data.get('mode')
        if mode != BUYER_BROADCAST_MODE_TEST:
            raise ValidationError('На этом этапе поддерживается только тестовый режим.')
        return mode

    def clean_test_contacts(self):
        contacts = self.cleaned_data.get('test_contacts')
        if not contacts:
            raise ValidationError('Выберите хотя бы один тестовый контакт.')
        max_recipients = get_buyer_broadcast_test_max_recipients()
        selected = list(contacts)
        if len(selected) > max_recipients:
            raise ValidationError(
                f'Максимум тестовых получателей: {max_recipients}.',
            )
        for buyer in selected:
            if not buyer.is_test_contact:
                raise ValidationError(
                    'Можно выбирать только контакты с признаком тестового.',
                )
        return contacts

    def clean_template_name(self):
        template_name = str(self.cleaned_data.get('template_name') or '').strip()
        if not template_name:
            raise ValidationError('Имя шаблона WhatsApp обязательно.')
        return template_name

    def clean_template_body_parameters(self):
        params = self.cleaned_data.get('template_body_parameters')
        if params is None:
            return []
        if not isinstance(params, list):
            raise ValidationError('Параметры шаблона должны быть списком.')
        for value in params:
            if isinstance(value, (dict, list, tuple, set)):
                raise ValidationError(
                    'Параметры шаблона не должны содержать вложенные структуры.',
                )
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise ValidationError(
                    'Каждый параметр шаблона должен быть строкой или числом.',
                )
        return params
