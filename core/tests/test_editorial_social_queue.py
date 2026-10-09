from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from core.models import EditorialPage, EditorialSocialDraft


class EditorialSocialQueueTests(TestCase):
    def test_only_published_articles_get_unique_unposted_social_captions(self):
        published = EditorialPage.objects.create(
            title='Guide approved', slug='approved-social-guide',
            meta_description='Approved guide', body='Verified facts',
            status='published')
        EditorialPage.objects.create(
            title='Guide unapproved', slug='draft-social-guide',
            meta_description='Unreviewed', body='Draft', status='draft')
        call_command('prepare_editorial_social_drafts', stdout=StringIO())
        call_command('prepare_editorial_social_drafts', stdout=StringIO())
        social = EditorialSocialDraft.objects.get(article=published)
        self.assertEqual(social.status, 'draft')
        self.assertIn('utm_source=instagram', social.caption)
        self.assertIn('/guide/parts/approved-social-guide/', social.caption)
        self.assertFalse(EditorialSocialDraft.objects.filter(article__slug='draft-social-guide').exists())
        self.assertEqual(EditorialSocialDraft.objects.filter(article=published).count(),1)
