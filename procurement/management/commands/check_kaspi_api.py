"""Explicit one-off read-only Kaspi diagnostics, never part of main worker startup."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Read-only official Kaspi API diagnostics. No catalogue publication.'

    def handle(self, *args, **options):
        from .process_price_updates import verify_catalog_readonly
        ok = verify_catalog_readonly()
        self.stdout.write(
            'Official Kaspi read check succeeded.' if ok else
            'Official Kaspi read check unavailable; review sanitized server logs.'
        )
