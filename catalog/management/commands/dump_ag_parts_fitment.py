import json

from django.core.management.base import BaseCommand

from catalog.ag_parts_fitment_audit import dump_ag_parts_cards


class Command(BaseCommand):
    help = (
        'Read-only снимок карточек AG Parts, Kaspi-привязок и комплектов ТО. '
        'Ничего не пишет.'
    )

    def handle(self, *args, **options):
        payload = dump_ag_parts_cards()
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
