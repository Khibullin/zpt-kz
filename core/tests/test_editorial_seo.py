from django.test import TestCase, override_settings
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

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_article_text_is_escaped_not_html(self):
        self.article.body = '<script>alert("unsafe")</script>'
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        response = self.client.get('/guide/parts/test-part-guide/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<script>alert("unsafe")</script>', html=False)
        self.assertContains(response, '&lt;script&gt;', html=False)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_sitemap_index_includes_content_only_when_enabled(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(response, '/sitemap-content.xml')
        with override_settings(SEO_EDITORIAL_ENABLED=False):
            response = self.client.get('/sitemap.xml')
            self.assertNotContains(response, '/sitemap-content.xml')

    def test_publication_timestamp_set_on_publish(self):
        self.assertIsNone(self.article.published_at)
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        self.article.refresh_from_db()
        self.assertIsNotNone(self.article.published_at)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_index_lists_only_approved_pages(self):
        self.assertNotContains(self.client.get('/guide/parts/'), 'Тестовый материал')
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        self.assertContains(self.client.get('/guide/parts/'), 'Тестовый материал')
        self.assertContains(self.client.get('/sitemap-content.xml'), '/guide/parts/')

    @override_settings(SEO_EDITORIAL_ENABLED=False)
    def test_index_disabled_by_default_flag(self):
        self.assertEqual(self.client.get('/guide/parts/').status_code, 404)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_unpublished_article_is_removed_from_index_and_sitemap(self):
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 200)
        self.article.status = EditorialPage.STATUS_DRAFT
        self.article.save()
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 404)
        self.assertNotContains(self.client.get('/guide/parts/'), 'Тестовый материал')
        self.assertNotContains(self.client.get('/sitemap-content.xml'), 'test-part-guide')

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_sitemap_escapes_article_urls(self):
        article = EditorialPage.objects.create(
            slug='guide--test',
            title='Дополнительный материал',
            meta_description='Дополнительная информация о выборе запчастей',
            body='Проверенные сведения о подборе запчастей.',
            status=EditorialPage.STATUS_PUBLISHED,
        )
        sitemap = self.client.get('/sitemap-content.xml')
        self.assertContains(sitemap, '/guide/parts/guide--test/')
        self.assertIsNotNone(article.published_at)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_related_products_only_show_active_linked_products(self):
        from catalog.models import Product

        active = Product.objects.create(
            title='Тестовый активный фильтр', slug='editorial-active-filter',
            article='DEMO-A', seller_name='Тест', whatsapp_number='+77000000000',
            status='active', price=5000,
        )
        hidden = Product.objects.create(
            title='Тестовый скрытый фильтр', slug='editorial-hidden-filter',
            article='DEMO-H', seller_name='Тест', whatsapp_number='+77000000000',
            status='hidden', price=5000,
        )
        self.article.related_products.add(active, hidden)
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        response = self.client.get('/guide/parts/test-part-guide/')
        self.assertContains(response, 'Тестовый активный фильтр')
        self.assertNotContains(response, 'Тестовый скрытый фильтр')
        self.assertContains(response, 'editorial-active-filter')

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_related_products_not_available_on_draft_article(self):
        self.assertEqual(self.client.get('/guide/parts/test-part-guide/').status_code, 404)

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_editorial_design_and_request_cta_are_present(self):
        self.article.status = EditorialPage.STATUS_PUBLISHED
        self.article.save()
        response = self.client.get('/guide/parts/test-part-guide/')
        self.assertContains(response, 'css/editorial.css')
        self.assertContains(response, '/request-parts/')
        self.assertContains(response, 'Полезные материалы')
