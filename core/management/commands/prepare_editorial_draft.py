from django.core.management.base import BaseCommand, CommandError
from core.editorial_drafts import prepare_editorial_draft


class Command(BaseCommand):
    help = 'Prepare one unpublished editorial draft from a candidate ID; never publish.'

    def add_arguments(self, parser):
        parser.add_argument('candidate_id', type=int)

    def handle(self, *args, **options):
        try:
            page = prepare_editorial_draft(options['candidate_id'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Draft #{page.pk}: {page.slug}; status={page.status}')
