"""Regression tests: unverified Apify comparisons are not exposed to buyer accounts."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    BuyingComparisonMaterial, KaspiPriceAudit, KaspiReviewTask,
    Membership, Offer, PermanentBuyingItem, Supplier,
)

SECOND = 'dataset_kaspi-api_2026-11-21_14-00-09-125.json'
FIRST = 'dataset_kaspi-api_2026-11-21_13-39-32-482(2).json'


class ApifyReviewComparisonPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = get_user_model().objects.create_user(
            'apify_viewer', password='test-password-123')
        cls.manager = get_user_model().objects.create_user(
            'apify_manager', password='test-password-123')
        Membership.objects.create(user=cls.viewer, role='user')
        Membership.objects.create(user=cls.manager, role='sales')
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(
            supplier=cls.supplier, brand='STINSS', article='S2S1552',
            name='Амортизатор передний', cost=Decimal('5603'), stock='4')
        cls.audit = KaspiPriceAudit.objects.create(
            offer=cls.offer, source_filename=SECOND,
            min_price=Decimal('12784'), product_id='179049603',
            card_url='https://kaspi.kz/shop/p/stinss-perednii-s2s1552-1-sht-179049603/',
            cost_snapshot=Decimal('5603'),
            commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'),
            profit=Decimal('5331'), roi=Decimal('60'),
            identity_status='needs_review')

    def details(self, offer, source):
        return reverse('procurement:comparison', args=[offer.pk]) + '?apify_source=' + source

    def test_unverified_second_export_is_staff_only_even_if_offer_previously_listed(self):
        url = self.details(self.offer, SECOND)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['review_only'])
        self.assertTrue(response.context['apify_second_review'])

        PermanentBuyingItem.objects.create(
            offer=self.offer, source='historical', product_id='179049603',
            title='Ранее занесённый товар',
            cost_snapshot=Decimal('5603'),
            kaspi_price_snapshot=Decimal('12784'),
            commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('5331'),
            roi_snapshot=Decimal('60'))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['review_only'])

    def test_unverified_srr_export_remains_staff_only(self):
        srr = Offer.objects.create(
            supplier=self.supplier, brand='SRR OE', article='SA-160004',
            name='Амортизатор задний', cost=Decimal('8284'), stock='9')
        KaspiReviewTask.objects.create(
            offer=srr, status='pending', priority=1,
            reason='Apify 21.11.2026: артикул SA-160004, Kaspi ID 179824674. '
                   'В Kaspi бренд SRR; у Omega SRR OE.')
        url = self.details(srr, FIRST)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['apify_srr_review'])
        self.assertTrue(response.context['review_only'])

    def test_export_specific_source_title_is_shown_without_candidate_entry(self):
        """14:00 snapshot's title is evidence for this card, not another export."""
        material = BuyingComparisonMaterial.objects.create(
            offer=self.offer, source_filename=SECOND,
            kaspi_product_id=self.audit.product_id,
            kaspi_url=self.audit.card_url,
            kaspi_status='local_snapshot',
            kaspi_data={
                'title': 'STINSS передний S2S1552 1 шт',
                'apify_image_product_id': '179049603',
                'images': ['https://resources.cdn-kaspi.kz/img/m/p/sample.jpg'],
            },
        )
        self.client.force_login(self.manager)
        response = self.client.get(self.details(self.offer, SECOND))
        self.assertEqual(response.status_code, 200)
        title = next(r for r in response.context['rows']
                     if r['label'] == 'Наименование товара')
        self.assertEqual(title['kaspi'], 'STINSS передний S2S1552 1 шт')
        self.assertTrue(response.context['review_only'])
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.identity_status, 'needs_review')

        # A title whose source photo belongs to a different product ID must
        # never be treated as an observation for this selected card.
        material.kaspi_data = {
            'title': 'Другой товар',
            'apify_image_product_id': '000000000',
        }
        material.save(update_fields=['kaspi_data'])
        response = self.client.get(self.details(self.offer, SECOND))
        title = next(r for r in response.context['rows']
                     if r['label'] == 'Наименование товара')
        self.assertEqual(title['kaspi'], 'Нет данных')
