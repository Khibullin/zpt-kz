import html
import re
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse

from core.models import Brand, CarModel, Country, PartCategory, Seller


class DismantlersDesignTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.country = Country.objects.create(name='Japan QA')
        cls.brand = Brand.objects.create(name='Toyota QA', country=cls.country)
        cls.model = CarModel.objects.create(name='Camry QA', brand=cls.brand)
        cls.category = PartCategory.objects.create(name='Engine QA')
        cls.seller = Seller.objects.create(
            name='Dismantler A', whatsapp='+77001234567', city='Алматы',
            transport_type='car', is_dismantler=True,
        )
        cls.seller.selected_brands.add(cls.brand)
        cls.seller.selected_models.add(cls.model)
        cls.seller.selected_countries.add(cls.country)
        cls.seller.selected_categories.add(cls.category)
        Seller.objects.create(name='Ordinary store', whatsapp='invalid', city='Астана',
                              transport_type='car', brand='Toyota QA')

    def test_gateway_keeps_both_existing_destinations_and_mobile_navigation(self):
        page = self.client.get('/sections/dismantle/')
        self.assertTemplateUsed(page, 'catalog/interface/dismantlers/landing.html')
        self.assertContains(page, '/find-part/?condition=used')
        self.assertContains(page, '/auto-dismantlers/')
        self.assertContains(page, 'class="mobile-nav"')
        self.assertNotContains(page, 'site-product-search')
        self.assertContains(self.client.get('/'), 'site-product-search')

    def test_combined_filters_preserve_selection_and_only_offer_directory_cities(self):
        filters = dict(city='Алматы', transport_type='car', brand=str(self.brand.pk),
                       model=str(self.model.pk), country=str(self.country.pk),
                       category=str(self.category.pk))
        page = self.client.get('/auto-dismantlers/', filters)
        self.assertEqual([s.pk for s in page.context['sellers']], [self.seller.pk])
        for key, value in filters.items():
            self.assertEqual(page.context['filters'][key], value)
        self.assertEqual(list(page.context['cities']), ['Алматы'])
        self.assertContains(page, 'form="dismantlers-search"')
        self.assertNotContains(self.client.get('/auto-dismantlers/', {'city': 'Астана'}),
                               '<h2><a href="/parts-seller/')

    def test_directory_searches_brands_categories_and_deduplicates(self):
        self.seller.selected_brands.add(Brand.objects.create(name='Toyota QA second', country=self.country))
        for query in ('Toyota QA', 'Engine QA'):
            page = self.client.get('/auto-dismantlers/', {'q': query})
            self.assertEqual(page.context['page_obj'].paginator.count, 1)
            self.assertContains(page, 'Dismantler A')
            self.assertNotContains(page, 'Ordinary store')
        ordinary = self.client.get('/parts-sellers/', {'q': 'Toyota QA'})
        self.assertEqual(ordinary.context['page_obj'].paginator.count, 0)

    def test_public_list_omits_images_and_preserves_contact_links(self):
        self.seller.directory_photo = 'seller_directory/actual-company.webp'
        self.seller.save(update_fields=['directory_photo'])
        page = self.client.get('/auto-dismantlers/')
        self.assertContains(page, reverse('parts_seller_detail_public', args=[self.seller.pk]))
        self.assertNotContains(page, 'seller_directory/actual-company.webp')
        self.assertContains(page, 'directory-profile')
        self.assertContains(page, 'tel:+77001234567')
        self.assertContains(page, 'https://wa.me/77001234567')
        self.assertEqual(self.client.get(reverse('parts_seller_detail_public', args=[self.seller.pk])).status_code, 200)
        self.seller.whatsapp = 'invalid'
        self.seller.save(update_fields=['whatsapp'])
        self.assertNotContains(self.client.get('/auto-dismantlers/'), 'class="dismantle-whatsapp"')

    def test_empty_and_no_match_states_are_distinct(self):
        page = self.client.get('/auto-dismantlers/', {'q': 'No such company'})
        self.assertContains(page, 'Авторазборы не найдены')
        self.assertNotContains(page, 'Каталог авторазборов наполняется')
        Seller.objects.filter(is_dismantler=True).update(is_active=False)
        page = self.client.get('/auto-dismantlers/')
        self.assertContains(page, 'Каталог авторазборов наполняется')
        self.assertNotContains(page, 'Dismantler A')

    def test_public_description_contacts_and_private_notes_are_separate(self):
        self.seller.directory_description = 'Контрактные детали <script>alert(1)</script>'
        self.seller.directory_description_kk = 'Қолданылған қосалқы бөлшектер'
        self.seller.notes = 'PRIVATE_AUDIT_MARKER'
        self.seller.phone2 = '+7 (707) 111-22-33'
        self.seller.save()
        url = reverse('parts_seller_detail_public', args=[self.seller.pk])
        page = self.client.get(url)
        self.assertTemplateUsed(page, 'catalog/interface/dismantlers/detail.html')
        self.assertContains(page, 'tel:+77071112233')
        self.assertContains(page, 'tel:+77001234567')
        self.assertContains(page, 'href="/auto-dismantlers/"')
        for page in (page, self.client.get('/auto-dismantlers/')):
            self.assertContains(page, 'Контрактные детали &lt;script&gt;')
            self.assertContains(page, 'Қолданылған қосалқы бөлшектер')
            self.assertNotContains(page, '<script>alert(1)</script>')
            self.assertNotContains(page, 'PRIVATE_AUDIT_MARKER')
        for query, count in [('PRIVATE_AUDIT_MARKER', 0), ('Контрактные детали', 1), ('Қолданылған', 1)]:
            page = self.client.get('/auto-dismantlers/', {'q': query})
            self.assertEqual(page.context['page_obj'].paginator.count, count)

    def test_blank_description_preserves_known_specialization_without_notes(self):
        self.seller.notes = 'PRIVATE_AUDIT_MARKER'
        self.seller.save(update_fields=['notes'])
        page = self.client.get(reverse('parts_seller_detail_public', args=[self.seller.pk]))
        for name in ('Japan QA', 'Toyota QA', 'Camry QA', 'Engine QA'):
            self.assertContains(page, name)
        self.assertNotContains(page, 'PRIVATE_AUDIT_MARKER')

    def test_paused_or_inactive_profiles_are_unavailable_and_ordinary_profile_is_unchanged(self):
        url = reverse('parts_seller_detail_public', args=[self.seller.pk])
        for changes in ({'is_paused': True}, {'is_paused': False, 'is_active': False}):
            Seller.objects.filter(pk=self.seller.pk).update(**changes)
            self.assertEqual(self.client.get(url).status_code, 404)
        ordinary = Seller.objects.get(name='Ordinary store')
        page = self.client.get(reverse('parts_seller_detail_public', args=[ordinary.pk]))
        self.assertTemplateUsed(page, 'catalog/parts_sellers/detail.html')

    def test_pagination_preserves_escaped_search_and_filters(self):
        query = 'A&B + Разбор'
        Seller.objects.bulk_create([
            Seller(name=f'{query} {i:02}', whatsapp='', is_dismantler=True,
                   city='Алматы', transport_type='car') for i in range(21)
        ])
        page = self.client.get('/auto-dismantlers/', {'q': query, 'city': 'Алматы'})
        links = re.findall(r'href="([^"]*page=2[^"]*)"', page.content.decode())
        self.assertEqual(len(links), 2)
        params = parse_qs(urlsplit(html.unescape(links[0])).query)
        self.assertEqual(params, {'q': [query], 'city': ['Алматы'], 'page': ['2']})
        second = self.client.get('/auto-dismantlers/' + html.unescape(links[0]))
        self.assertEqual(len(second.context['sellers']), 1)

    def test_compact_directory_omits_duplicate_address_and_technical_labels(self):
        self.seller.name = 'ALDI MART • Instagram photos and videos'
        self.seller.address = 'улица Абая, 10'
        self.seller.market_location = 'улица Абая, 10'
        self.seller.brand = 'Multi'
        self.seller.category = 'Multi'
        self.seller.save()
        page = self.client.get('/auto-dismantlers/')
        self.assertContains(page, self.seller.address, count=1)
        self.assertContains(page, 'ALDI MART')
        self.assertNotContains(page, 'Instagram photos and videos')
        self.assertNotContains(page, 'Multi')
        cards = re.findall(r'<article class="directory-card">(.*?)</article>', page.content.decode(), re.S)
        self.assertTrue(cards)
        self.assertTrue(all('<img' not in card for card in cards))
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.name, 'ALDI MART • Instagram photos and videos')
