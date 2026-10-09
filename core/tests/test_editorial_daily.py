from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from catalog.models import Brand, Country, Product
from core.models import EditorialCandidate, EditorialPage


class EditorialDailyPipelineTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Daily-test-country')
        brand = Brand.objects.create(name='Daily-test-brand', country=country)
        for i in range(4):
            Product.objects.create(
                title=f'Daily test part {i}', slug=f'daily-test-{i}', article=f'DEMO-{i}',
                description='Unverified seller description; please check fitment.',
                brand=brand, seller_name='Test seller', whatsapp_number='+77000000000',
                status='active',
            )

    def test_daily_defaults_to_two_unpublished_drafts(self):
        call_command('run_editorial_daily', stdout=StringIO())
        self.assertEqual(EditorialPage.objects.filter(status='draft', source_candidate__isnull=False).count(), 2)
        self.assertEqual(EditorialPage.objects.filter(status='published', source_candidate__isnull=False).count(), 0)

    def test_second_run_uses_new_candidates_without_duplicates(self):
        call_command('run_editorial_daily', stdout=StringIO())
        call_command('run_editorial_daily', stdout=StringIO())
        self.assertEqual(EditorialPage.objects.filter(source_candidate__isnull=False).count(), 4)
        self.assertEqual(EditorialCandidate.objects.count(), 4)

    def test_hard_limit(self):
        with self.assertRaises(CommandError):
            call_command('run_editorial_daily', '--limit', '3', stdout=StringIO())

    def test_ai_flag_requires_explicit_enablement(self):
        with self.assertRaises(CommandError):
            call_command('run_editorial_daily', '--ai', stdout=StringIO())
        self.assertEqual(EditorialPage.objects.filter(source_candidate__isnull=False).count(), 0)
