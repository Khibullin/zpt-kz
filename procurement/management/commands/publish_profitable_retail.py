from collections import Counter
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from procurement.retail_bulk import publication_candidates, publish_candidate


class Command(BaseCommand):
    help = 'Preview or publish verified profitable offers at Kaspi minus 5%.'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        user = get_user_model().objects.get(username=options['username'])
        candidates = publication_candidates(user)
        self.stdout.write(str(dict(Counter(row[0].supplier.name for row in candidates))))
        if options['apply']:
            self.stdout.write(str(dict(Counter(publish_candidate(row[0].pk, user) for row in candidates))))
