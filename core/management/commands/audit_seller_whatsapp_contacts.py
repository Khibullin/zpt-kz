"""Read-only audit of excluded city numbers and replacement contacts."""
import json

from django.core.management.base import BaseCommand
from core.models import Seller
from core.phone_utils import is_excluded_city_whatsapp, seller_whatsapp_phone
from service_requests.models import ServiceSeller


class Command(BaseCommand):
    help = 'Проверить назначение WhatsApp для кодов 327/727 без изменения данных и отправки сообщений.'

    def handle(self, *args, **options):
        checked = 0
        rows = []
        for kind, qs in (
            ('parts', Seller.objects.only('id', 'name', 'whatsapp', 'phone2').order_by('id')),
            ('services', ServiceSeller.objects.only('id', 'name', 'whatsapp', 'catalog_notes').order_by('id')),
        ):
            for seller in qs.iterator():
                checked += 1
                if not is_excluded_city_whatsapp(seller.whatsapp):
                    continue
                selected = seller_whatsapp_phone(seller)
                rows.append({'kind': kind, 'id': seller.pk, 'name': seller.name,
                             'call_phone': seller.whatsapp, 'whatsapp_phone': selected,
                             'action': 'next_contact' if selected else 'hide_whatsapp'})
        self.stdout.write(json.dumps({'checked': checked, 'affected': len(rows),
            'next_contact': sum(bool(row['whatsapp_phone']) for row in rows),
            'without_replacement': sum(not row['whatsapp_phone'] for row in rows),
            'rows': rows}, ensure_ascii=False, indent=2))
