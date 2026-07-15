from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import BuyerContact, BuyerPortalAccess, Request
from core.phone_utils import normalize_kz_phone
from core.services.buyer_contact_service import (
    SYNC_STATUS_REQUEST_LINK_CONFLICT,
    SYNC_STATUS_SKIPPED_INVALID_PHONE,
    SYNC_STATUS_SYNCED,
    sync_buyer_contact_from_request,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Связать заявки с BuyerContact и пересчитать агрегаты покупателей. '
        'По умолчанию работает в режиме dry-run.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Выполнить запись в базу (по умолчанию только прогноз).',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Размер пакета заявок при --apply.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Ограничить количество обрабатываемых заявок.',
        )
        parser.add_argument(
            '--request-id',
            type=int,
            default=None,
            help='Обработать только одну заявку по ID.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        batch_size = max(1, options['batch_size'])
        limit = options['limit']
        request_id = options['request_id']

        queryset = Request.objects.order_by('id')
        if request_id is not None:
            queryset = queryset.filter(pk=request_id)
        if limit is not None:
            queryset = queryset[:limit]

        requests = list(queryset)
        report = self._build_dry_run_report(requests)

        self.stdout.write(
            f"Режим: {'APPLY' if apply_changes else 'DRY RUN'}",
        )
        self._print_report(report, dry_run=not apply_changes)

        if not apply_changes:
            return

        apply_report = self._apply_sync(requests, batch_size=batch_size)
        self.stdout.write('')
        self.stdout.write('Результат применения:')
        self._print_apply_report(apply_report)

    def _build_dry_run_report(self, requests: list[Request]) -> dict:
        existing_buyer_phones = set(
            BuyerContact.objects.values_list('phone_normalized', flat=True),
        )
        report = {
            'total': len(requests),
            'already_linked': 0,
            'not_linked': 0,
            'valid_phones': 0,
            'invalid_phones': 0,
            'predicted_new_buyers': 0,
            'existing_buyers': 0,
            'request_link_conflicts': 0,
            'portal_conflicts': 0,
        }

        seen_new_phones: set[str] = set()
        seen_portal_conflict_phones: set[str] = set()

        for request_obj in requests:
            if request_obj.buyer_contact_id:
                report['already_linked'] += 1
            else:
                report['not_linked'] += 1

            normalized_phone = normalize_kz_phone(request_obj.phone)
            if not normalized_phone:
                report['invalid_phones'] += 1
                continue

            report['valid_phones'] += 1

            if request_obj.buyer_contact_id:
                existing_buyer = request_obj.buyer_contact
                if (
                    existing_buyer
                    and existing_buyer.phone_normalized != normalized_phone
                ):
                    report['request_link_conflicts'] += 1
                continue

            if normalized_phone in existing_buyer_phones:
                report['existing_buyers'] += 1
            elif normalized_phone not in seen_new_phones:
                report['predicted_new_buyers'] += 1
                seen_new_phones.add(normalized_phone)

            if (
                normalized_phone not in seen_portal_conflict_phones
                and self._has_portal_conflict(normalized_phone)
            ):
                report['portal_conflicts'] += 1
                seen_portal_conflict_phones.add(normalized_phone)

        return report

    def _has_portal_conflict(self, normalized_phone: str) -> bool:
        alt_phone = None
        if normalized_phone.startswith('7') and len(normalized_phone) == 11:
            alt_phone = '8' + normalized_phone[1:]
        canonical = BuyerPortalAccess.objects.filter(
            phone_normalized=normalized_phone,
        ).exists()
        alt = (
            BuyerPortalAccess.objects.filter(phone_normalized=alt_phone).exists()
            if alt_phone
            else False
        )
        return canonical and alt

    def _apply_sync(self, requests: list[Request], *, batch_size: int) -> dict:
        report = {
            'linked': 0,
            'skipped': 0,
            'request_link_conflicts': 0,
            'portal_conflicts': 0,
            'vehicles_created': 0,
            'categories_created': 0,
            'cities_created': 0,
            'errors': 0,
        }
        affected_buyer_ids: set[int] = set()

        for offset in range(0, len(requests), batch_size):
            batch = requests[offset:offset + batch_size]
            for request_obj in batch:
                try:
                    result = sync_buyer_contact_from_request(
                        request_obj,
                        rebuild=False,
                    )
                except Exception as exc:
                    report['errors'] += 1
                    logger.exception(
                        'Failed to sync buyer contact for request #%s: %s',
                        request_obj.pk,
                        type(exc).__name__,
                    )
                    self.stderr.write(
                        f'Ошибка заявки #{request_obj.pk}: {type(exc).__name__}',
                    )
                    continue

                if result.status == SYNC_STATUS_SYNCED:
                    report['linked'] += 1
                    if result.buyer_id:
                        affected_buyer_ids.add(result.buyer_id)
                    if result.portal_conflict:
                        report['portal_conflicts'] += 1
                elif result.status == SYNC_STATUS_REQUEST_LINK_CONFLICT:
                    report['request_link_conflicts'] += 1
                    report['skipped'] += 1
                elif result.status == SYNC_STATUS_SKIPPED_INVALID_PHONE:
                    report['skipped'] += 1
                else:
                    report['skipped'] += 1

        for buyer_id in sorted(affected_buyer_ids):
            try:
                buyer = BuyerContact.objects.get(pk=buyer_id)
                with transaction.atomic():
                    from core.services.buyer_contact_service import _rebuild_buyer_contact

                    stats = _rebuild_buyer_contact(buyer)
                report['vehicles_created'] += stats.vehicles_created
                report['categories_created'] += stats.categories_created
                report['cities_created'] += stats.cities_created
            except Exception as exc:
                report['errors'] += 1
                logger.exception(
                    'Failed to rebuild buyer contact #%s: %s',
                    buyer_id,
                    type(exc).__name__,
                )
                self.stderr.write(
                    f'Ошибка пересчёта покупателя #{buyer_id}: {type(exc).__name__}',
                )

        return report

    def _print_report(self, report: dict, *, dry_run: bool) -> None:
        self.stdout.write(f"Всего заявок рассмотрено: {report['total']}")
        self.stdout.write(f"Уже связаны: {report['already_linked']}")
        self.stdout.write(f"Не связаны: {report['not_linked']}")
        self.stdout.write(f"Корректные номера: {report['valid_phones']}")
        self.stdout.write(f"Некорректные номера: {report['invalid_phones']}")
        if dry_run:
            self.stdout.write(
                f"Прогноз новых покупателей: {report['predicted_new_buyers']}",
            )
            self.stdout.write(
                f"Существующие покупатели: {report['existing_buyers']}",
            )
            self.stdout.write(
                f"Конфликты Request.buyer_contact: {report['request_link_conflicts']}",
            )
            self.stdout.write(
                f"Конфликты BuyerPortalAccess: {report['portal_conflicts']}",
            )
        else:
            self.stdout.write('Создано автомобилей: 0')
            self.stdout.write('Создано категорий: 0')
            self.stdout.write('Создано городских интересов: 0')

    def _print_apply_report(self, report: dict) -> None:
        self.stdout.write(f"Успешно связано: {report['linked']}")
        self.stdout.write(f"Пропущено: {report['skipped']}")
        self.stdout.write(
            f"Конфликты Request.buyer_contact: {report['request_link_conflicts']}",
        )
        self.stdout.write(
            f"Конфликты BuyerPortalAccess: {report['portal_conflicts']}",
        )
        self.stdout.write(f"Создано автомобилей: {report['vehicles_created']}")
        self.stdout.write(f"Создано категорий: {report['categories_created']}")
        self.stdout.write(
            f"Создано городских интересов: {report['cities_created']}",
        )
        self.stdout.write(f"Ошибок: {report['errors']}")
