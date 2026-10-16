"""Regression checks for the two owner-approved landing pages."""
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product
from service_requests.models import ServiceSeller


class LandingPagesTests(TestCase):
    def test_home_tiles_preserve_original_request_and_dismantler_flows(self):
        page = self.client.get('/')
        destinations = [(entry['key'], entry['url']) for entry in page.context['interface_tiles']]
        self.assertEqual(destinations, [
            ('parts', reverse('interface_find_part')),
            ('dismantle', reverse('interface_section', kwargs={'section': 'dismantle'})),
            ('consumables', reverse('consumables_landing')),
            ('serviceHub', reverse('services_landing')),
            ('wholesale', reverse('public_seller_wholesale', kwargs={'slug': 'ag-parts'})),
        ])
        self.assertNotContains(page, 'Оставить заявку')
        self.assertNotContains(page, 'Авторазборы / Б/У запчасти')
        self.assertContains(page, 'Авторазборы')
        self.assertContains(page, reverse('parts_sellers_catalog_public'))

    def test_consumables_uses_only_existing_active_products(self):
        product = Product.objects.create(
            title='Фильтр масляный тест', article='REAL-TO-1',
            price=4321, seller_name='Seller', whatsapp_number='+77001234567',
            status='active',
        )
        Product.objects.create(
            title='Свеча скрытая', article='HIDDEN-TO-1',
            price=321, seller_name='Seller', whatsapp_number='+77001234567',
            status='hidden',
        )
        page = self.client.get(reverse('consumables_landing'))
        self.assertTemplateUsed(page, 'catalog/interface/consumables.html')
        self.assertContains(page, product.article)
        self.assertNotContains(page, 'HIDDEN-TO-1')
        self.assertContains(page, '/?q=фильтр')
        self.assertContains(page, '/?q=свеча')
        self.assertContains(page, reverse('maintenance_kit_list'))
        self.assertContains(page, reverse('ag_parts_filter_finder'))
        self.assertEqual([p.pk for p in page.context['products']], [product.pk])

    def test_services_page_uses_real_active_organizations_only(self):
        seller = ServiceSeller.objects.create(
            name='Test Service Workshop', whatsapp='+77001234566',
            city='Алматы', seller_type='sto', is_active=True,
        )
        ServiceSeller.objects.create(
            name='Hidden Service Workshop', whatsapp='+77001234565',
            city='Алматы', seller_type='sto', is_active=False,
        )
        ServiceSeller.objects.create(
            name='Test-only Workshop', whatsapp='+77001234564',
            city='Алматы', seller_type='sto', is_active=True,
            is_test_seller=True,
        )
        page = self.client.get(reverse('services_landing'))
        self.assertTemplateUsed(page, 'catalog/interface/services_landing.html')
        self.assertContains(page, seller.name)
        self.assertNotContains(page, 'Hidden Service Workshop')
        self.assertNotContains(page, 'Test-only Workshop')
        self.assertContains(page, reverse('interface_section', kwargs={'section': 'repair'}))
        self.assertContains(page, reverse('interface_section', kwargs={'section': 'detailing'}))
        self.assertContains(page, reverse('services_catalog'))
        self.assertContains(page, reverse('service_seller_detail', kwargs={'seller_id': seller.pk}))
