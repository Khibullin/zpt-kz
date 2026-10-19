"""SEO discovery must never alter seller publication or request-dispatch behavior."""
import json
import re

from django.test import TestCase

from catalog.models import Brand, CarModel, Country, MaintenanceKit
from core.models import Seller
from service_requests.models import ServiceSeller


class DirectorySeoExpansionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parts = [
            Seller.objects.create(
                name=f'Алматы магазин {i}',
                city='Алматы',
                address=f'ул. Тестовая, {i}',
                whatsapp=f'7701000100{i}',
                sells_new_parts=True,
            )
            for i in range(3)
        ]
        cls.dismantlers = [
            Seller.objects.create(
                name=f'Астана разбор {i}',
                city='Астана',
                address=f'ул. Пример, {i}',
                whatsapp=f'7701000200{i}',
                is_dismantler=True,
            )
            for i in range(3)
        ]
        cls.sto = [
            ServiceSeller.objects.create(
                name=f'Шымкент СТО {i}',
                city='Шымкент',
                address=f'ул. Сервисная, {i}',
                whatsapp=f'7701000300{i}',
                seller_type='sto',
            )
            for i in range(3)
        ]
        cls.detailers = [
            ServiceSeller.objects.create(
                name=f'Тараз детейлинг {i}',
                city='Тараз',
                address=f'ул. Полировки, {i}',
                whatsapp=f'7701000400{i}',
                seller_type='detailing',
            )
            for i in range(3)
        ]
        cls.hidden = Seller.objects.create(
            name='Заблокированный магазин',
            city='Алматы',
            whatsapp='77010005000',
            sells_new_parts=True,
            is_paused=True,
        )

    def test_city_landings_require_real_public_organizations(self):
        cases = {
            '/parts-sellers/almaty/': 'Магазины автозапчастей в Алматы',
            '/auto-dismantlers/astana/': 'Авторазборы в Астана',
            '/sto/shymkent/': 'СТО и ремонт автомобилей в Шымкент',
            '/detailing/taraz/': 'Детейлинг и тюнинг в Тараз',
        }
        for path, heading in cases.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)
                self.assertContains(response, f'<link rel="canonical" href="https://zpt.kz{path}">', html=True)
        self.assertEqual(self.client.get('/parts-sellers/karaganda/').status_code, 404)
        self.assertEqual(self.client.get('/parts-sellers/unknown/').status_code, 404)
        self.assertNotContains(self.client.get('/parts-sellers/almaty/'), self.hidden.name)

    def test_city_sitemap_includes_only_eligible_routes(self):
        result = self.client.get('/sitemap-cities.xml')
        self.assertEqual(result.status_code, 200)
        content = result.content.decode()
        self.assertIn('/parts-sellers/almaty/', content)
        self.assertIn('/auto-dismantlers/astana/', content)
        self.assertIn('/sto/shymkent/', content)
        self.assertIn('/detailing/taraz/', content)
        self.assertNotIn('/sto/almaty/', content)

    def test_organization_sitemap_deduplicates_mixed_seller(self):
        mixed = Seller.objects.create(
            name='Смешанный продавец',
            city='Алматы',
            whatsapp='77010006000',
            sells_new_parts=True,
            is_dismantler=True,
        )
        content = self.client.get('/sitemap-organizations.xml').content.decode()
        self.assertIn(f'/parts-seller/{mixed.pk}/', content)
        self.assertEqual(content.count(f'<loc>https://zpt.kz/parts-seller/{mixed.pk}/</loc>'), 1)
        self.assertIn(f'/catalog/services/{self.sto[0].pk}/', content)
        self.assertNotIn(f'/parts-seller/{self.hidden.pk}/', content)

    def test_kit_sitemap_only_contains_published_kits(self):
        country = Country.objects.create(name='SEO Country')
        brand = Brand.objects.create(name='SEO Brand', country=country)
        model = CarModel.objects.create(name='SEO Model', brand=brand)
        MaintenanceKit.objects.create(
            slug='seo-published-kit', name='SEO published kit',
            brand=brand, car_model=model, is_active=True,
        )
        MaintenanceKit.objects.create(
            slug='seo-hidden-kit', name='SEO hidden kit',
            brand=brand, car_model=model, is_active=False,
        )
        content = self.client.get('/sitemap-kits.xml').content.decode()
        self.assertIn('/maintenance-kits/seo-published-kit/', content)
        self.assertNotIn('/maintenance-kits/seo-hidden-kit/', content)

    def test_structured_data_uses_public_company_fields_only(self):
        company = self.parts[0]
        company.notes = 'PRIVATE INTERNAL NOTE'
        company.save(update_fields=['notes'])
        response = self.client.get(f'/parts-seller/{company.pk}/')
        scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), flags=re.S)
        records = [json.loads(item) for item in scripts]
        organization = next(item for item in records if item.get('@type') == 'AutoPartsStore')
        self.assertEqual(organization['name'], company.name)
        self.assertEqual(organization['address']['addressLocality'], 'Алматы')
        self.assertNotIn('PRIVATE INTERNAL NOTE', response.content.decode())

    def test_filtered_city_path_and_alt_directory_views_are_noindex(self):
        city = self.client.get('/parts-sellers/almaty/?q=anything')
        self.assertEqual(city['X-Robots-Tag'], 'noindex, follow')
        alias = self.client.get(f'/parts-seller/{self.parts[0].pk}/?directory=parts')
        self.assertEqual(alias['X-Robots-Tag'], 'noindex, follow')
        self.assertEqual(self.client.get('/service-request/filters/')['X-Robots-Tag'], 'noindex, nofollow')
