from django.test import TestCase, override_settings
from catalog.models import Brand, Country, Product
from core.models import EditorialCandidate, EditorialPage
from core.editorial_drafts import prepare_editorial_draft


class EditorialDraftTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Draft-test-country')
        brand = Brand.objects.create(name='Draft-test-brand', country=country)
        self.product = Product.objects.create(
            title='Demo air filter', article='DEMO-001', slug='demo-air-filter',
            description='Reference information as entered by seller.',
            brand=brand, status='active', seller_name='Demo seller',
            whatsapp_number='+77000000000',
        )
        self.candidate = EditorialCandidate.objects.create(
            source_key=f'product:{self.product.pk}',
            title='Проверка подбора фильтра DEMO-001',
            rationale='Требует проверки',
            source_product=self.product,
        )

    @override_settings(SEO_EDITORIAL_ENABLED=True)
    def test_creates_unpublished_draft_only(self):
        page = prepare_editorial_draft(self.candidate.pk)
        self.assertEqual(page.status, EditorialPage.STATUS_DRAFT)
        self.assertEqual(self.client.get(f'/guide/parts/{page.slug}/').status_code, 404)
        self.assertNotIn(page.slug, self.client.get('/sitemap-content.xml').content.decode())
        self.assertEqual(list(page.related_products.all()), [self.product])
        self.assertIn('DEMO-001', page.body)

    def test_repeat_is_idempotent(self):
        first = prepare_editorial_draft(self.candidate.pk)
        second = prepare_editorial_draft(self.candidate.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EditorialPage.objects.filter(source_candidate=self.candidate).count(), 1)

    def test_inactive_product_fails_closed(self):
        self.product.status = 'hidden'
        self.product.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            prepare_editorial_draft(self.candidate.pk)
        self.assertEqual(EditorialPage.objects.filter(source_candidate=self.candidate).count(), 0)
