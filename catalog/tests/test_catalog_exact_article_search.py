from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Brand,
    CarModel,
    Category,
    Country,
    Product,
    ProductPriceTier,
    SellerProfile,
)


def _product(**kwargs):
    defaults = {
        'title': 'Тестовая запчасть',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'city': 'Алматы',
        'article': 'T151109111',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _ids(response):
    return [product.pk for product in response.context['products']]


class CatalogExactArticleSearchTests(TestCase):
    def test_exact_article_ignores_negative_mentions(self):
        exact = _product(
            title='Фильтр T151109111',
            slug='exact-t151109111',
            article='T151109111',
        )
        mixed = _product(
            title='Фильтр 151000151AA',
            slug='false-151000151aa',
            article='151000151AA',
            description='Не смешивать с T151109111',
        )
        confused = _product(
            title='Фильтр F081109111HD',
            slug='false-f081109111hd',
            article='F081109111HD',
            compatibility='Не путать с T15-1109111 / T151109111',
        )

        response = self.client.get(reverse('catalog_list'), {'q': 'T151109111'})

        self.assertEqual(response.status_code, 200)
        ids = _ids(response)
        self.assertEqual(ids, [exact.pk])
        self.assertNotIn(mixed.pk, ids)
        self.assertNotIn(confused.pk, ids)

    def test_exact_article_matches_normalized_formatting(self):
        product = _product(
            title='Фильтр с дефисом в артикуле',
            slug='normalized-t151109111',
            article='T15-1109111',
        )
        for query in ('T151109111', 'T15-1109111', 'T15 1109111', 't15-1109111'):
            response = self.client.get(reverse('catalog_list'), {'q': query})
            self.assertEqual(response.status_code, 200, query)
            self.assertEqual(_ids(response), [product.pk], query)

    def test_missing_exact_article_does_not_search_text(self):
        _product(
            title='Другой фильтр',
            slug='other-article-mention',
            article='OTHER123',
            description='Не путать с T151109999',
        )
        response = self.client.get(reverse('catalog_list'), {'q': 'T151109999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_ids(response), [])

    def test_plain_text_search_still_uses_title_description_and_compatibility(self):
        by_title = _product(
            title='воздушный фильтр двигателя',
            slug='air-filter-title',
            article='VF-100',
        )
        by_description = _product(
            title='Картридж фильтра',
            slug='air-filter-description',
            article='VF-200',
            description='воздушный фильтр в сборе',
        )
        by_compatibility = _product(
            title='Вставка фильтра',
            slug='air-filter-compatibility',
            article='VF-300',
            compatibility='воздушный фильтр для Tiggo 7',
        )
        unrelated = _product(
            title='Тормозные колодки',
            slug='brake-pads-text',
            article='BRK-100',
            description='Колодки передние',
        )

        response = self.client.get(reverse('catalog_list'), {'q': 'воздушный фильтр'})
        ids = set(_ids(response))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids, {by_title.pk, by_description.pk, by_compatibility.pk})
        self.assertNotIn(unrelated.pk, ids)

        tiggo = self.client.get(reverse('catalog_list'), {'q': 'Tiggo 7'})
        self.assertEqual(_ids(tiggo), [by_compatibility.pk])


class CatalogExactArticleFilterTests(TestCase):
    def setUp(self):
        self.china = Country.objects.create(name='Китай exact')
        self.japan = Country.objects.create(name='Япония exact')
        self.chery = Brand.objects.create(country=self.china, name='Chery exact')
        self.toyota = Brand.objects.create(country=self.japan, name='Toyota exact')
        self.tiggo = CarModel.objects.create(brand=self.chery, name='Tiggo 7 exact')
        self.camry = CarModel.objects.create(brand=self.toyota, name='Camry exact')
        self.filters = Category.objects.create(name='Фильтры exact')
        self.pads = Category.objects.create(name='Колодки exact')
        self.kept = _product(
            title='Нужный фильтр exact',
            slug='exact-filter-kept',
            article='T151109111',
            city='Алматы',
            brand=self.chery,
            car_model=self.tiggo,
            category=self.filters,
        )
        ProductPriceTier.objects.create(product=self.kept, min_qty=2, price=900)
        self.other_city = _product(
            title='Тот же артикул другой город',
            slug='exact-filter-other-city',
            article='T15-1109111',
            city='Астана',
            brand=self.chery,
            car_model=self.tiggo,
            category=self.filters,
        )
        self.other_category = _product(
            title='Тот же артикул другая категория',
            slug='exact-filter-other-category',
            article='t15-1109111',
            city='Алматы',
            brand=self.chery,
            car_model=self.tiggo,
            category=self.pads,
        )
        self.other_vehicle = _product(
            title='Тот же артикул другая марка',
            slug='exact-filter-other-vehicle',
            article='T15 1109111',
            city='Алматы',
            brand=self.toyota,
            car_model=self.camry,
            category=self.filters,
        )
        user = User.objects.create_user(username='exact-offer-seller', password='secret12345')
        SellerProfile.objects.create(
            user=user,
            name='Exact Offer Seller',
            phone='77001112244',
            city='Алматы',
        )
        self.client.login(username='exact-offer-seller', password='secret12345')

    def _search(self, **params):
        params.setdefault('q', 'T151109111')
        return self.client.get(reverse('catalog_list'), params)

    def test_existing_filters_still_narrow_exact_article_results(self):
        all_matches = _ids(self._search())
        self.assertCountEqual(
            all_matches,
            [self.kept.pk, self.other_city.pk, self.other_category.pk, self.other_vehicle.pk],
        )
        self.assertEqual(len(all_matches), len(set(all_matches)))

        by_city = _ids(self._search(city='Алматы'))
        self.assertIn(self.kept.pk, by_city)
        self.assertNotIn(self.other_city.pk, by_city)
        self.assertEqual(by_city.count(self.kept.pk), 1)

        by_category = _ids(self._search(category=str(self.filters.pk)))
        self.assertIn(self.kept.pk, by_category)
        self.assertNotIn(self.other_category.pk, by_category)

        by_country = _ids(self._search(country=str(self.china.pk)))
        self.assertIn(self.kept.pk, by_country)
        self.assertNotIn(self.other_vehicle.pk, by_country)
        self.assertEqual(by_country.count(self.kept.pk), 1)

        by_brand = _ids(self._search(brand=str(self.chery.pk)))
        self.assertIn(self.kept.pk, by_brand)
        self.assertNotIn(self.other_vehicle.pk, by_brand)
        self.assertEqual(by_brand.count(self.kept.pk), 1)

        by_model = _ids(self._search(model=str(self.tiggo.pk)))
        self.assertIn(self.kept.pk, by_model)
        self.assertNotIn(self.other_vehicle.pk, by_model)
        self.assertEqual(by_model.count(self.kept.pk), 1)

        by_offer = _ids(self._search(offer='wholesale'))
        self.assertEqual(by_offer, [self.kept.pk])
