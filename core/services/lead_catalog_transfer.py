"""Explicit admin transfers into the catalog; no account, invitation or dispatch."""
import hashlib

from django.db import connection, transaction
from django.utils import timezone

from core.models import Seller, SellerLead, SellerLeadContactCandidate
from core.services.seller_lead_admin_workflow import (
    WorkflowActionResult, WorkflowResultKind, normalize_request_seller_whatsapp,
)
from service_requests.models import ServiceSeller


def _notes(lead):
    parts = [lead.profile_description, lead.notes, f'SellerLead #{lead.pk}',
             f'Источник: {lead.source_url}',
             'WhatsApp опубликован в источнике; работоспособность не проверена.']
    parts += [f'Адрес: {loc.address}' for loc in lead.locations.all() if loc.address]
    parts += [f'Контакт: {c.value} — {c.label}; источник: {c.source_url}'
              for c in lead.contact_candidates.exclude(status=SellerLeadContactCandidate.STATUS_REJECTED)]
    return '\n'.join(part for part in parts if part)


def transfer_lead_to_catalog(lead, *, target):
    """Target is explicitly chosen by the administrator, never inferred from a name.

    Lock the lead, recheck identity, and preserve all existing target records.
    Ambiguous identities remain in the found list for review.
    """
    def result(kind, message, **kwargs):
        return WorkflowActionResult(kind, f'{lead.name}: {message}', lead.pk, **kwargs)

    with transaction.atomic():
        lead = SellerLead.objects.select_for_update().get(pk=lead.pk)
        if target not in ('sto', 'seller', 'dismantler'):
            return result(WorkflowResultKind.ERROR, 'неизвестный раздел')
        phone = normalize_request_seller_whatsapp(lead.whatsapp)
        if len(phone) != 11 or not phone.startswith('7'):
            return result(WorkflowResultKind.ERROR, 'требуется корректный WhatsApp Казахстана')
        if not all((lead.name.strip(), lead.city.strip(), lead.source_url.strip(), lead.whatsapp_source_url.strip())):
            return result(WorkflowResultKind.ERROR, 'нужны название, город, источник и источник WhatsApp')
        if lead.duplicate_of_id or lead.review_status == SellerLead.REVIEW_REJECTED:
            return result(WorkflowResultKind.ERROR, 'карточка отклонена или отмечена дублем')
        if lead.contact_candidates.filter(status=SellerLeadContactCandidate.STATUS_CONFLICT).exists():
            return result(WorkflowResultKind.ERROR, 'сначала разрешите конфликт контактов')
        # An explicit target action confirms the type for manually collected leads.
        allowed = {
            'sto': {SellerLead.BUSINESS_TYPE_UNKNOWN, SellerLead.BUSINESS_TYPE_SERVICE_ONLY, SellerLead.BUSINESS_TYPE_SERVICE_PARTS},
            'seller': {SellerLead.BUSINESS_TYPE_UNKNOWN, SellerLead.BUSINESS_TYPE_NEW_PARTS, SellerLead.BUSINESS_TYPE_WHOLESALER, SellerLead.BUSINESS_TYPE_DEALER, SellerLead.BUSINESS_TYPE_SERVICE_PARTS, SellerLead.BUSINESS_TYPE_MIXED},
            'dismantler': {SellerLead.BUSINESS_TYPE_UNKNOWN, SellerLead.BUSINESS_TYPE_DISMANTLER, SellerLead.BUSINESS_TYPE_MIXED},
        }
        if lead.business_type not in allowed[target]:
            return result(WorkflowResultKind.ERROR, 'выбранный раздел противоречит типу бизнеса')
        model = ServiceSeller if target == 'sto' else Seller
        link = 'service_seller' if target == 'sto' else 'request_seller'
        linked_id = getattr(lead, link + '_id')
        # Serialize imports of the same phone across different leads on production PostgreSQL.
        if connection.vendor == 'postgresql':
            key = int.from_bytes(hashlib.sha256(f'catalog:{model._meta.label}:{phone}'.encode()).digest()[:8], 'big', signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [key])
        # Check normalized primary/additional phones and existing source links.
        ids = {obj.pk for obj in model.objects.all()
               if phone in {normalize_request_seller_whatsapp(obj.whatsapp),
                            normalize_request_seller_whatsapp(getattr(obj, 'phone2', ''))}}
        if target == 'sto':
            ids.update(ServiceSeller.objects.filter(map_link=lead.source_url).values_list('pk', flat=True))
        source_ids = SellerLead.objects.filter(source_url=lead.source_url).exclude(**{link: None}).values_list(link, flat=True)
        ids.update(source_ids)
        if linked_id:
            ids.add(linked_id)
        if len(ids) > 1:
            return result(WorkflowResultKind.ERROR, 'неоднозначный дубль; перенос не выполнен')
        created = False
        if ids:
            seller = model.objects.get(pk=next(iter(ids)))
            if normalize_request_seller_whatsapp(seller.whatsapp) != phone:
                return result(WorkflowResultKind.ERROR, 'контакт существующей записи отличается; требуется проверка')
            if target == 'dismantler' and not seller.is_dismantler:
                return result(WorkflowResultKind.WARNING, f'продавец №{seller.pk} уже существует; проверьте признак авторазбора')
        else:
            if model.objects.filter(name__iexact=lead.name.strip(), city__iexact=lead.city.strip()).exists():
                return result(WorkflowResultKind.ERROR, 'совпали название и город; проверьте возможный дубль')
            payload = dict(name=lead.name.strip(), whatsapp=phone, city=lead.city.strip(),
                           catalog_only=True, receive_requests=False, is_active=True,
                           is_test_seller=False, is_paused=False)
            if target == 'sto':
                location = lead.locations.exclude(address='').order_by('pk').first()
                address = location.address if location else ''
                if len(address) > 255 or len(lead.source_url) > 200:
                    return result(WorkflowResultKind.ERROR, 'адрес или ссылка превышает длину поля; требуется проверка')
                payload.update(password='', user=None, seller_type='sto', address=address,
                               map_link=lead.source_url, description=lead.profile_description,
                               catalog_notes=_notes(lead))
            else:
                transport = lead.request_seller_transport_type
                if transport not in ('car', 'truck'):
                    return result(WorkflowResultKind.ERROR, 'выберите подтверждённый тип транспорта')
                payload.update(password_hash='', user=None, transport_type=transport,
                               is_dismantler=target == 'dismantler', notes=_notes(lead))
            seller = model.objects.create(**payload)
            created = True
        setattr(lead, link, seller)
        lead.reviewed_at = lead.reviewed_at or timezone.now()
        lead.save(update_fields=[link, 'reviewed_at', 'updated_at'])
        return result(WorkflowResultKind.SUCCESS if created else WorkflowResultKind.WARNING,
                      f'{"создана карточка" if created else "связана существующая карточка"} №{seller.pk}; '
                      + ('вход и получение заявок выключены' if created else 'её настройки сохранены'),
                      seller_id=seller.pk, created_seller=created, linked_existing_seller=not created)
