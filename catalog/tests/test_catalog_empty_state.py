from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Brand, Country, Product, SellerProfile


class CatalogEmptyStateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='empty_state_seller',
            password='testpass123',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='Empty State Seller',
            phone='77001112233',
            city='Алматы',
        )

    def _create_product(self, *, title, article, slug, brand=None):
        return Product.objects.create(
            title=title,
            slug=slug,
            article=article,
            price=1000,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            status='active',
            city='Алматы',
            brand=brand,
        )

    def test_search_without_results_shows_empty_state(self):
        self._create_product(
            title='Фильтр воздушный',
            article='AIR-100',
            slug='air-filter-empty-a',
        )
        query = 'несуществующий-артикул-xyz'
        response = self.client.get(reverse('catalog_list'), {'q': query})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'В каталоге пока нет запчасти')
        self.assertContains(response, query)
        self.assertContains(response, 'href="/request-parts/"')
        self.assertContains(response, 'Оставить заявку продавцам')

    def test_home_without_search_hides_empty_state(self):
        self._create_product(
            title='Тормозные колодки',
            article='BRK-200',
            slug='brake-pads-home',
        )
        response = self.client.get(reverse('catalog_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'В каталоге пока нет запчасти')
        self.assertNotContains(
            response,
            'В каталоге пока нет подходящих запчастей по выбранным параметрам',
        )
        self.assertNotContains(response, 'Оставить заявку продавцам')

    def test_search_with_results_hides_empty_state(self):
        product = self._create_product(
            title='Амортизатор передний',
            article='SHK-300',
            slug='shock-absorber-hit',
        )
        response = self.client.get(
            reverse('catalog_list'),
            {'q': 'Амортизатор'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, product.title)
        self.assertNotContains(response, 'В каталоге пока нет запчасти')
        self.assertNotContains(response, 'Оставить заявку продавцам')

    def test_filters_without_query_show_filter_empty_state(self):
        country = Country.objects.create(name='Япония')
        brand_with_stock = Brand.objects.create(
            country=country,
            name='Toyota',
        )
        brand_empty = Brand.objects.create(
            country=country,
            name='EmptyBrandXYZ',
        )
        self._create_product(
            title='Свеча зажигания',
            article='SPK-400',
            slug='spark-plug-toyota',
            brand=brand_with_stock,
        )

        response = self.client.get(
            reverse('catalog_list'),
            {'brand': str(brand_empty.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'В каталоге пока нет подходящих запчастей по выбранным параметрам',
        )
        self.assertContains(response, 'href="/request-parts/"')
        self.assertContains(response, 'Оставить заявку продавцам')
        self.assertNotContains(response, 'В каталоге пока нет запчасти')
