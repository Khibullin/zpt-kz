from unittest.mock import patch
from django.http import HttpResponse
from django.test import TestCase


class OrganizationEntryTests(TestCase):
    def test_entry_destinations(self):
        cases = [
            ('/find-part/', 'Найти запчасть', '/find-part/?request=1', '/parts-sellers/'),
            ('/sections/repair/', 'СТО и ремонт', '/service-request/?type=sto', '/catalog/services/?type=sto'),
            ('/sections/detailing/', 'Детейлинг и тюнинг', '/service-request/?type=detailing', '/catalog/services/?type=detailing'),
        ]
        for url, title, form, catalog in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertTemplateUsed(response, 'catalog/interface/organization_entry.html')
                self.assertContains(response, title)
                self.assertContains(response, f'href="{form}"')
                self.assertContains(response, f'href="{catalog}"')
                self.assertNotContains(response, 'id="home-parts-form"')

    def test_existing_parts_requests_and_results_keep_original_handler(self):
        with patch('catalog.views.catalog_list', return_value=HttpResponse('original')) as original:
            for query in ['request=1', 'condition=used', 'home_request=existing-token', 'q=filter&city=Астана']:
                self.client.get('/find-part/?' + query)
                self.assertEqual(original.call_args.args[0].path, '/find-part/')
                self.assertTrue(original.call_args.args[0].GET)
            self.client.post('/find-part/', {'example': 'retained'})
            self.assertEqual(original.call_args.args[0].POST['example'], 'retained')
            self.assertEqual(original.call_count, 5)

    def test_existing_filtered_service_links_keep_directory(self):
        with patch('service_requests.catalog_pages.service_directory', return_value=HttpResponse('directory')) as directory:
            for section in ['repair', 'detailing']:
                self.client.get(f'/sections/{section}/?city=Астана')
                self.assertEqual(directory.call_args.kwargs['section'], section)
                self.assertEqual(directory.call_args.args[0].GET['city'], 'Астана')
