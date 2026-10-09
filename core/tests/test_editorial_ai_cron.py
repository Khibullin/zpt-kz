from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from catalog.models import Brand, Country, Product
from core.models import EditorialPage, EditorialCandidate, EditorialAIExecution


class EditorialAICronTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Cron-AI-country')
        brand = Brand.objects.create(name='Cron-AI-brand', country=country)
        self.pages = []
        for i in range(3):
            product = Product.objects.create(
                title=f'Cron-AI filter {i}', article=f'AI-TEST-{i}',
                slug=f'cron-ai-filter-{i}', description='Seller description',
                brand=brand, seller_name='Test',
                whatsapp_number='+77000000000',
            )
            candidate = EditorialCandidate.objects.create(
                source_key=f'product:{product.pk}', title='Cron AI test',
                source_product=product, rationale='test',
            )
            self.pages.append(EditorialPage.objects.create(
                title='Cron AI test', slug=f'cron-ai-guide-{i}',
                meta_description='Example', body='Draft text',
                source_candidate=candidate,
            ))

    def post(self):
        return self.client.post('/internal/editorial/ai/',
            HTTP_AUTHORIZATION='Bearer '+('x'*40))

    def test_unauthorized(self):
        self.assertEqual(self.client.post('/internal/editorial/ai/').status_code,404)

    @override_settings(EDITORIAL_AI_ENABLED=True, OPENAI_API_KEY='mock',
                       EDITORIAL_CRON_TOKEN='x'*40)
    @patch('core.editorial_ai_cron.improve_draft_with_ai')
    def test_two_attempt_cap_and_no_publication(self, improve):
        improve.side_effect = lambda pk: EditorialPage.objects.get(pk=pk)
        self.assertEqual(self.post().json()['status'], 'improved')
        self.assertEqual(self.post().json()['status'], 'improved')
        self.assertEqual(self.post().json()['status'], 'daily_limit')
        self.assertEqual(EditorialAIExecution.objects.filter(day=timezone.localdate()).count(), 2)
        self.assertEqual(EditorialPage.objects.filter(pk__in=[p.pk for p in self.pages], status='published').count(), 0)

    @override_settings(EDITORIAL_AI_ENABLED=False, OPENAI_API_KEY='mock',
                       EDITORIAL_CRON_TOKEN='x'*40)
    @patch('core.editorial_ai_cron.improve_draft_with_ai')
    def test_disabled(self, improve):
        self.assertEqual(self.post().status_code,503)
        improve.assert_not_called()
