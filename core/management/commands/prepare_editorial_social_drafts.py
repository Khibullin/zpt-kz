"""Create unposted social drafts for published editorial pages."""
from django.core.management.base import BaseCommand
from core.editorial_quality import instagram_caption_for_article
from core.models import EditorialPage, EditorialSocialDraft


class Command(BaseCommand):
    help = 'Build Instagram caption drafts; never post to Instagram.'

    def handle(self, *args, **opts):
        created = 0
        for page in EditorialPage.objects.filter(status=EditorialPage.STATUS_PUBLISHED).order_by('pk'):
            if EditorialSocialDraft.objects.filter(article=page).exists():
                continue
            caption = instagram_caption_for_article(page)
            _, added = EditorialSocialDraft.objects.get_or_create(
                article=page, defaults={'caption': caption, 'status':'draft'}
            )
            created += bool(added)
        self.stdout.write(f'Social drafts created: {created}')
