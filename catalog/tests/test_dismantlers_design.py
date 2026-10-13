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

    def test_public_card_uses_real_profile_photo_and_valid_whatsapp(self):
        self.seller.directory_photo = 'seller_directory/actual-company.webp'
        self.seller.save(update_fields=['directory_photo'])
        page = self.client.get('/auto-dismantlers/')
        self.assertContains(page, reverse('parts_seller_detail_public', args=[self.seller.pk]))
        self.assertContains(page, 'seller_directory/actual-company.webp')
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

    def test_pagination_preserves_escaped_search_and_filters(self):
        query = 'A&B + Разбор'
        Seller.objects.bulk_create([
            Seller(name=f'{query} {i:02}', whatsapp='', is_dismantler=True,
                   city='Алматы', transport_type='car') for i in range(21)
        ])
        page = self.client.get('/auto-dismantlers/', {'q': query, 'city': 'Алматы'})
        links = re.findall(r'href="([^"]*page=2[^"]*)"', page.content.decode())
        self.assertEqual(len(links), 1)
        params = parse_qs(urlsplit(html.unescape(links[0])).query)
        self.assertEqual(params, {'q': [query], 'city': ['Алматы'], 'page': ['2']})
        second = self.client.get('/auto-dismantlers/' + html.unescape(links[0]))
        self.assertEqual(len(second.context['sellers']), 1)
