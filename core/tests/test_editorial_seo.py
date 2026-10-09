from django.test import TestCase, override_settings
from django.urls import reverse
from core.models import EditorialPage


class EditorialSEOTests(TestCase):
    def setUp(self):
        self.article = EditorialPage.objects.create(
            slug='test-part-guide',
            title='Тестовый материал',
            meta_description='Проверяемый материал о запчастях',
            body='Информация должна быть проверена до публикации.',
        )

    def test_draft_is_not_public_or_in_sitemap(self):
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 404)
        response = self.client.get('/sitemap-content.xml')
        self.assertNotIn('test-part-guide', response.content.decode())

    def test_review_is_not_public(self):
        self.article.status = EditorialPage.STATUS_REVIEW
        self.article.save()
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 404)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_published_article_is_public_and_in_sitemap(self):
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        page = self.client.get('/guide/parts/test-part-guide/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Тестовый материал')
        self.assertContains(page, 'https://zpt.kz/guide/parts/test-part-guide/')
        sitemap = self.client.get('/sitemap-content.xml')
        self.assertContains(sitemap, 'https://zpt.kz/guide/parts/test-part-guide/')

    @override_settings(SEO_EDITORIAL_ENABLED=False)
    def test_feature_switch_disables_sitemap(self):
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        self.assertNotIn('test-part-guide', self.client.get('/sitemap-content.xml').content.decode())
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 404)
