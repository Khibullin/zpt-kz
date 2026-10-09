from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from core.editorial_quality import editorial_release_errors, instagram_caption_for_article


class Article:
    status = 'draft'
    source_candidate_id = 1
    title = 'Тест подбора фильтра'
    meta_description = 'Как сверить детали'
    slug = 'test-guide'
    body = ('Сверьте номер фильтра с каталогом производителя. '
            'Совместимость конкретной модели не подтверждена. ' * 20)


class EditorialReleaseTests(SimpleTestCase):
    def test_unverified_seller_data_does_not_pass(self):
        page = Article()
        self.assertTrue(editorial_release_errors(page))
        self.assertTrue(editorial_release_errors(page, evidence_confirmed=True))

    def test_verified_source_plus_editor_confirmation(self):
        page = Article()
        page.body += '\nИсточник https://www.mann-filter.com/example'
        self.assertFalse(editorial_release_errors(page, evidence_confirmed=True))

    def test_caption_requires_published_page(self):
        page = Article()
        with self.assertRaises(ValidationError):
            instagram_caption_for_article(page)
        page.status = 'published'
        caption = instagram_caption_for_article(page)
        self.assertIn('https://zpt.kz/guide/parts/test-guide/', caption)
        self.assertIn('#автозапчасти', caption)
