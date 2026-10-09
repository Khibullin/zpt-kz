from django.test import TestCase
from django.core.management import call_command
from io import StringIO
from catalog.models import Product, Brand, Country
from core.models import EditorialCandidate, EditorialPage


class CandidateCollectorTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Test-country')
        brand = Brand.objects.create(name='Test-brand', country=country)
        Product.objects.create(
            title='Test automotive filter', article='DEMO-FILTER', slug='demo-filter',
            description='Product listing description; no fitment asserted.',
            status='active', brand=brand, seller_name='Demo',
            whatsapp_number='+77000000000',
        )

    def test_preview_does_not_write(self):
        call_command('collect_editorial_candidates', stdout=StringIO())
        self.assertEqual(EditorialCandidate.objects.count(), 0)

    def test_save_creates_only_candidate_and_is_idempotent(self):
        call_command('collect_editorial_candidates', '--save', stdout=StringIO())
        call_command('collect_editorial_candidates', '--save', stdout=StringIO())
        self.assertEqual(EditorialCandidate.objects.count(), 1)
        self.assertEqual(EditorialPage.objects.filter(title__icontains='Test automotive filter').count(), 0)
        self.assertEqual(EditorialCandidate.objects.first().status, 'new')
