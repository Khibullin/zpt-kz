"""Daily editorial pipeline: select up to N candidates, create private drafts.

LLM enhancement remains a separately controlled switch; no article is published.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.editorial_ai import improve_draft_with_ai
from core.editorial_candidates import collect_product_candidates
from core.editorial_drafts import prepare_editorial_draft
from core.models import EditorialCandidate


class Command(BaseCommand):
    help = 'Create at most two private editorial drafts. Never publish.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=2)
        parser.add_argument('--ai', action='store_true', help='Opt-in OpenAI rewrite of the private draft.')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 2:
            raise CommandError('Daily safety limit must be 1 or 2')
        if options['ai'] and not getattr(settings, 'EDITORIAL_AI_ENABLED', False):
            raise CommandError('EDITORIAL_AI_ENABLED must be explicitly enabled')
        if options['ai'] and not getattr(settings, 'OPENAI_API_KEY', ''):
            raise CommandError('OPENAI_API_KEY must be configured')

        results = []
        # Reuse unprocessed candidate backlog first; do not drop previously queued topics.
        ids = list(
            EditorialCandidate.objects.filter(status='new', draft_page__isnull=True)
            .order_by('created_at', 'pk').values_list('pk', flat=True)[:limit]
        )
        if len(ids) < limit:
            collect_product_candidates(limit=limit - len(ids), create=True)
            additional = list(
                EditorialCandidate.objects.filter(status='new', draft_page__isnull=True)
                .exclude(pk__in=ids)
                .order_by('created_at', 'pk').values_list('pk', flat=True)[:limit-len(ids)]
            )
            ids.extend(additional)

        for pk in ids:
            try:
                page = prepare_editorial_draft(pk)
                outcome = 'created'
                if options['ai']:
                    try:
                        improve_draft_with_ai(page.pk)
                        outcome = 'ai_rewritten'
                    except Exception as exc:
                        # Preserve the template draft; surface error to job logs.
                        self.stderr.write(f'AI rewrite failed for draft {page.pk}: {type(exc).__name__}')
                        outcome = 'draft_kept_ai_failed'
                results.append((page.pk, outcome))
            except (ValueError, EditorialCandidate.DoesNotExist) as exc:
                self.stderr.write(f'Candidate {pk} skipped: {exc}')

        self.stdout.write(f'Private drafts processed: {len(results)} / {limit}; AI={options["ai"]}')
        for pk, outcome in results:
            self.stdout.write(f'  draft={pk} outcome={outcome}')
