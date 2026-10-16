from django.test import TestCase
from django.urls import reverse
from catalog.models import Product
from core.models import Seller


class InterfaceReleaseTests(TestCase):
    def test_home_shows_five_live_destinations_without_duplicate_directory_tiles(self):
        page = self.client.get('/')
        self.assertTemplateUsed(page, 'catalog/interface/home.html')
        html = page.content.decode()
        self.assertEqual(len(page.context['interface_tiles']), 5)
        self.assertEqual(html.count('class="tile tile-'), 5)
        self.assertEqual(html.count('class="directory"'), 0)
        self.assertNotContains(page, 'DEMO-')
        self.assertContains(page, 'class="hero hero-compact"')
        self.assertNotContains(page, 'Оставить заявку')
        self.assertContains(page, 'data-zpt-language')
        self.assertContains(page, 'class="mobile-nav"')
        self.assertContains(page, 'class="footer-desktop"')
        self.assertContains(page, reverse('consumables_landing'))
        self.assertContains(page, reverse('services_landing'))

    def test_only_active_real_products_paginate_without_duplicates(self):
        for i in range(14):
            Product.objects.create(title=f'Live part {i}', status='active', price=1200,
                article=f'LIVE-{i}', whatsapp_number='+77000000000', seller_name='Seller')
        Product.objects.create(title='Unpublished part', status='draft', price=1200)
        first = self.client.get('/')
        second = self.client.get('/', {'showcase_page':2})
        ids1={p.pk for p in first.context['products']}
        ids2={p.pk for p in second.context['products']}
        self.assertEqual(len(ids1),12)
        self.assertEqual(len(ids2),2)
        self.assertFalse(ids1 & ids2)
        self.assertContains(first, 'id="showcase-more"')
        self.assertNotContains(second, 'id="showcase-more"')
        self.assertNotContains(first,'Unpublished part')

    def test_dismantler_directory_is_separate_and_hides_inactive_profiles(self):
        Seller.objects.create(name='Real dismantler',whatsapp='+77000000001',transport_type='car',is_dismantler=True)
        Seller.objects.create(name='Parts store only',whatsapp='+77000000002',transport_type='car')
        Seller.objects.create(name='Paused dismantler',whatsapp='+77000000003',transport_type='car',is_dismantler=True,is_paused=True)
        page=self.client.get('/auto-dismantlers/')
        self.assertContains(page,'Real dismantler')
        self.assertNotContains(page,'Parts store only')
        self.assertNotContains(page,'Paused dismantler')
        self.assertContains(page,'/sections/dismantle/')
        self.assertContains(self.client.get('/parts-sellers/'),'Parts store only')

    def test_request_form_still_uses_existing_matched_endpoint(self):
        page=self.client.get('/find-part/',{'condition':'used'})
        self.assertContains(page,reverse('create_home_parts_request'))
        self.assertContains(page,'home-parts-form-v8.js')
        self.assertContains(page,'Б/У:')
        self.assertContains(page,'Ищем подходящих продавцов по всему Казахстану')

    def test_gateways_use_real_forms_and_correct_service_filters(self):
        for kind in ['repair','detailing']:
            page=self.client.get(f'/sections/{kind}/')
            value='sto' if kind=='repair' else 'detailing'
            self.assertContains(page,f'/service-request/?type={value}')
            self.assertContains(page,f'/catalog/services/?type={value}')
            self.assertContains(page,'href="/"')
        self.assertContains(self.client.get('/sections/dismantle/'),'/auto-dismantlers/')
        self.assertEqual(self.client.get('/sections/unknown/').status_code,404)

    def test_seller_can_save_directory_flag_without_changing_dispatch(self):
        seller=Seller.objects.create(name='Owner',whatsapp='+77001234567',transport_type='car',city='Алматы',receive_requests=True)
        session=self.client.session
        session['seller_id']=seller.pk
        session.save()
        from core.views import seller_profile, update_seller_profile
        from django.test import RequestFactory
        factory=RequestFactory()
        request=factory.post('/api/update-seller-profile/',data={'is_dismantler':True},content_type='application/json')
        request.session=session
        response=update_seller_profile(request)
        self.assertEqual(response.status_code,200,response.content)
        seller.refresh_from_db()
        self.assertTrue(seller.is_dismantler)
        self.assertTrue(seller.receive_requests)
        self.assertEqual(seller.seller_type,'seller')
        request=factory.get('/api/seller-profile/')
        request.session=session
        import json
        self.assertTrue(json.loads(seller_profile(request).content)['is_dismantler'])
