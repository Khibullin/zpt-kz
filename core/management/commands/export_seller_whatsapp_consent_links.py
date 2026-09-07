from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import (
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    Seller,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_whatsapp_consent import (
    SellerWhatsAppConsentError,
    build_seller_whatsapp_consent_url,
    get_seller_whatsapp_marketing_consent_status,
)


class Command(BaseCommand):
    help = (
        'Сформировать CSV со ссылками подтверждения WhatsApp-согласия. '
        'Ничего не отправляет.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            required=True,
            help='Путь к CSV-файлу.',
        )
        parser.add_argument(
            '--include-granted',
            action='store_true',
            help='Включить продавцов с уже данным согласием.',
        )
        parser.add_argument(
            '--include-revoked',
            action='store_true',
            help='Включить продавцов с отозванным согласием.',
        )

    def handle(self, *args, **options):
        output = Path(options['output'])
        include_granted = bool(options['include_granted'])
        include_revoked = bool(options['include_revoked'])

        sellers = Seller.objects.filter(
            is_active=True,
            is_test_seller=False,
        ).order_by('id')

        rows = []
        for seller in sellers.iterator():
            phone = normalize_kz_phone(seller.whatsapp)
            if not phone:
                continue
            status = get_seller_whatsapp_marketing_consent_status(seller)
            is_operational = bool(seller.receive_requests) and not seller.is_paused
            if status == CONTACT_CONSENT_STATUS_GRANTED:
                if not include_granted or not is_operational:
                    continue
            elif status == CONTACT_CONSENT_STATUS_REVOKED:
                if not include_revoked:
                    continue
            elif not is_operational:
                continue
            try:
                consent_url = build_seller_whatsapp_consent_url(seller)
            except SellerWhatsAppConsentError:
                continue
            rows.append({
                'seller_id': seller.pk,
                'name': seller.name,
                'whatsapp': seller.whatsapp,
                'phone_normalized': phone,
                'current_consent_status': status or '',
                'consent_url': consent_url,
            })

        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        'seller_id',
                        'name',
                        'whatsapp',
                        'phone_normalized',
                        'current_consent_status',
                        'consent_url',
                    ],
                )
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            raise CommandError(f'Не удалось записать CSV: {exc}') from exc

        self.stdout.write(self.style.SUCCESS(f'Записано строк: {len(rows)}'))
        self.stdout.write(str(output))
