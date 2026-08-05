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

    def _assert_single_empty_state(self, html):
        self.assertEqual(
            html.count('id="catalog-empty-state"'),
            1,
            msg='Expected exactly one catalog empty-state block',
        )
        self.assertEqual(
            html.count('Запчасть не найдена в каталоге'),
            1,
        )

    def test_search_without_results_shows_empty_state(self):
        self._create_product(
            title='Фильтр воздушный',
            article='AIR-100',
            slug='air-filter-empty-a',
        )
        query = 'несуществующий-артикул-xyz'
        response = self.client.get(reverse('catalog_list'), {'q': query})
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Запчасть не найдена в каталоге')
        self.assertContains(response, f'Вы искали: {query}')
        self.assertContains(response, 'Оставить заявку на запчасть')
        self.assertContains(response, 'href="/request-parts/"')
        self.assertContains(response, 'Сбросить поиск')
        self.assertContains(response, f'href="{reverse("catalog_list")}"')
        self._assert_single_empty_state(html)
        self.assertNotContains(response, 'Запчасти в наличии')

    def test_home_without_search_hides_empty_state(self):
        self._create_product(
            title='Тормозные колодки',
            article='BRK-200',
            slug='brake-pads-home',
        )
        response = self.client.get(reverse('catalog_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Запчасть не найдена в каталоге')
        self.assertNotContains(response, 'Оставить заявку на запчасть')
        self.assertNotContains(response, 'Сбросить поиск')
        self.assertNotContains(response, 'id="catalog-empty-state"')

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
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, product.title)
        self.assertContains(response, 'Запчасти в наличии')
        self.assertRegex(
            html,
            r'id="catalog-results"[^>]*data-catalog-scroll="results"',
        )
        self.assertIn(
            '#catalog-results[data-catalog-scroll="results"]',
            html,
        )
        self.assertNotContains(response, 'Запчасть не найдена в каталоге')
        self.assertNotContains(response, 'Оставить заявку на запчасть')
        self.assertNotContains(response, 'id="catalog-empty-state"')

    def test_home_without_search_does_not_enable_results_autoscroll(self):
        self._create_product(
            title='Фара передняя',
            article='LMP-700',
            slug='headlight-home-scroll',
        )
        response = self.client.get(reverse('catalog_list'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="catalog-results"')
        self.assertNotRegex(
            html,
            r'id="catalog-results"[^>]*data-catalog-scroll="results"',
        )
        self.assertNotContains(response, 'id="catalog-empty-state"')
        self.assertIn(
            '#catalog-results[data-catalog-scroll="results"]',
            html,
        )

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
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Запчасть не найдена в каталоге')
        self.assertContains(response, 'Оставить заявку на запчасть')
        self.assertContains(response, 'Сбросить поиск')
        self.assertContains(response, 'href="/request-parts/"')
        self.assertNotContains(response, 'Вы искали:')
        self._assert_single_empty_state(html)
        self.assertNotContains(response, 'id="catalog-results"')
        self.assertIn('getElementById(\'catalog-empty-state\')', html)


class CatalogHeroLayoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='hero_layout_seller',
            password='testpass123',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='Hero Layout Seller',
            phone='77001112244',
            city='Алматы',
        )
        Product.objects.create(
            title='Ремень ГРМ',
            slug='timing-belt-hero',
            article='BLT-500',
            price=2500,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            status='active',
            city='Алматы',
        )

    def test_home_hero_has_compact_action_blocks(self):
        response = self.client.get(reverse('catalog_list'))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Автозапчасти для легковых и грузовых автомобилей',
        )
        self.assertContains(response, '300+ продавцов по Казахстану')
        self.assertContains(response, 'Поиск по артикулу или названию')
        self.assertContains(response, 'class="search-btn"')
        self.assertContains(response, 'Найти')
        self.assertContains(response, 'Подбор по марке и модели')
        self.assertContains(response, 'Найти по авто')
        self.assertContains(response, 'Не нашли нужную запчасть?')
        self.assertContains(response, 'class="b2c-request-banner-btn"')
        self.assertContains(response, 'Оставить заявку')
        self.assertContains(response, 'href="/request-parts/"')

        self.assertNotContains(response, 'hero-perks')
        self.assertNotContains(
            response,
            'Напишите, какая запчасть нужна — продавцы найдут её для вас',
        )
        self.assertEqual(html.count('id="catalog-empty-state"'), 0)
        self.assertEqual(html.count('Запчасть не найдена в каталоге'), 0)

    def test_empty_state_not_duplicated_on_failed_search(self):
        response = self.client.get(
            reverse('catalog_list'),
            {'q': 'полностью-отсутствующий-товар-zzz'},
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('id="catalog-empty-state"'), 1)
        self.assertEqual(html.count('Запчасть не найдена в каталоге'), 1)
        self.assertContains(response, 'Найти по авто')
        self.assertContains(response, 'Не нашли нужную запчасть?')
