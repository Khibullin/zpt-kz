from django.core.management.base import BaseCommand, CommandError
from core.editorial_ai import improve_draft_with_ai


class Command(BaseCommand):
    help = 'Opt-in AI rewrite for an existing private draft; never publish.'
    def add_arguments(self, parser):
        parser.add_argument('page_id', type=int)
    def handle(self, *args, **opts):
        try:
            page = improve_draft_with_ai(opts['page_id'])
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Updated private draft #{page.pk}; status={page.status}')
