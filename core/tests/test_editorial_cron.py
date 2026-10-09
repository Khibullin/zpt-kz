from django.test import TestCase, override_settings
from catalog.models import Product, Country, Brand
from core.models import EditorialPage, EditorialDailyExecution


class EditorialCronTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Cron-test-country')
        brand = Brand.objects.create(name='Cron-test-brand', country=country)
        for i in range(3):
            Product.objects.create(
                title=f'Cron filter {i}', article=f'CRON-{i}', slug=f'cron-filter-{i}',
                description='Description awaiting review.', brand=brand,
                seller_name='Test', whatsapp_number='+77000000000',
            )
        self.url = '/internal/editorial/daily/'

    @override_settings(EDITORIAL_CRON_ENABLED=True, EDITORIAL_CRON_TOKEN='test-only-token')
    def test_bearer_token_and_once_per_day(self):
        self.assertEqual(self.client.post(self.url).status_code, 404)
        headers = {'HTTP_AUTHORIZATION':'Bearer test-only-token'}
        response = self.client.post(self.url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'complete')
        self.assertEqual(EditorialPage.objects.filter(source_candidate__isnull=False, status='draft').count(), 2)
        again = self.client.post(self.url, **headers)
        self.assertEqual(again.json()['status'], 'already_executed')
        self.assertEqual(EditorialPage.objects.filter(source_candidate__isnull=False).count(), 2)
        self.assertEqual(EditorialDailyExecution.objects.count(), 1)

    @override_settings(EDITORIAL_CRON_ENABLED=False, EDITORIAL_CRON_TOKEN='test-only-token')
    def test_disabled_never_creates_draft(self):
        response = self.client.post(self.url, HTTP_AUTHORIZATION='Bearer test-only-token')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(EditorialPage.objects.filter(source_candidate__isnull=False).exists())

    def test_without_configured_secret_returns_404(self):
        self.assertEqual(self.client.post(self.url).status_code, 404)
