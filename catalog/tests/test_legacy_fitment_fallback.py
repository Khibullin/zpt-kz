from django.test import TestCase
from django.urls import reverse

from catalog.legacy_fitment import legacy_text_supports_model
from catalog.models import Brand, CarModel, Category, Country, Product


def _product(**kwargs):
    defaults = {
        'title': 'Тестовая запчасть',
        'article': 'LEGACY-FIT',
        'price': 1000,
        'seller_name': 'Legacy Fitment Shop',
        'whatsapp_number': '77015550301',
        'status': 'active',
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class LegacyFitmentTextTests(TestCase):
    def test_negative_model_clause_is_rejected(self):
        self.assertFalse(
            legacy_text_supports_model(
                title='Салонный фильтр Chery M11 / M12',
                compatibility=(
                    'Chery M11 / M12. '
                    'Chery Tiggo 7 этим номером не подтверждаем.'
                ),
                model_name='Tiggo 7',
                sibling_model_names=['Tiggo 7 Pro', 'Tiggo 7 Pro Max'],
            )
        )

    def test_prefix_model_does_not_match_longer_sibling(self):
        self.assertFalse(
            legacy_text_supports_model(
                title='Фильтр Changan CS35 Plus',
                compatibility='Changan CS35 Plus 1.6. CS35 без Plus не входит.',
                model_name='CS35',
                sibling_model_names=['CS35 Plus'],
            )
        )

    def test_longer_sibling_model_matches_itself(self):
        self.assertTrue(
            legacy_text_supports_model(
                title='Фильтр Changan CS35 Plus',
                compatibility='Changan CS35 Plus 1.6 JL478QEP.',
                model_name='CS35 Plus',
                sibling_model_names=['CS35'],
            )
        )


class CatalogLegacyFitmentFallbackTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай LEGACY FIT')
        self.chery = Brand.objects.create(country=self.country, name='Chery Legacy Fit')
        self.changan = Brand.objects.create(country=self.country, name='Changan Legacy Fit')
        self.toyota = Brand.objects.create(country=self.country, name='Toyota Legacy Fit')

        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo7pro = CarModel.objects.create(brand=self.chery, name='Tiggo 7 Pro')
        self.m11 = CarModel.objects.create(brand=self.chery, name='M11')
        self.cs35 = CarModel.objects.create(brand=self.changan, name='CS35')
        self.cs35plus = CarModel.objects.create(brand=self.changan, name='CS35 Plus')

        self.filters = Category.objects.create(name='Фильтры Legacy Fit')
        self.suspension = Category.objects.create(name='Подвеска Legacy Fit')

    def _get(self, brand, model, **extra):
        params = {
            'brand': str(brand.pk),
            'model': str(model.pk),
        }
        params.update(extra)
        return self.client.get(reverse('catalog_list'), params)

    def _ids(self, response):
        return [item.pk for item in response.context['products']]

    def test_exact_structured_match_wins_over_legacy_fallback(self):
        exact = _product(
            title='Точный фильтр Tiggo 7',
            article='EXACT-T7',
            brand=self.chery,
            car_model=self.tiggo7,
            category=self.filters,
            compatibility='Chery Tiggo 7',
        )
        _product(
            title='Legacy фильтр Tiggo 7',
            article='LEGACY-T7',
            brand=self.chery,
            category=self.filters,
            compatibility='Chery Tiggo 7, 1.5T',
        )

        response = self._get(self.chery, self.tiggo7)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), [exact.pk])
        self.assertFalse(response.context['legacy_model_fallback'])
        self.assertNotContains(response, 'Точных совпадений по модели')

    def test_positive_text_only_fitment_is_used_when_exact_is_empty(self):
        legacy = _product(
            title='Салонный фильтр Chery Tiggo 7',
            article='LEGACY-T7-ONLY',
            brand=self.chery,
            category=self.filters,
            compatibility='Chery Tiggo 7 — 1.5T. Перед заказом проверьте VIN.',
        )

        response = self._get(self.chery, self.tiggo7)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), [legacy.pk])
        self.assertTrue(response.context['legacy_model_fallback'])
        self.assertContains(response, 'Точных совпадений по модели')
        self.assertContains(
            response,
            'Применяемость найдена по тексту старой карточки. Проверьте по VIN.',
        )
        self.assertNotContains(response, 'Запчасть не найдена в каталоге')

    def test_negative_mention_does_not_create_false_tiggo7_match(self):
        _product(
            title='Салонный фильтр Chery M11 / M12',
            article='M11-NOT-T7',
            brand=self.chery,
            category=self.filters,
            compatibility=(
                'Chery M11 / M12. OEM M11-8107915. '
                'Chery Tiggo 7 этим номером не подтверждаем.'
            ),
        )

        response = self._get(self.chery, self.tiggo7)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), [])
        self.assertFalse(response.context['legacy_model_fallback'])
        self.assertContains(response, 'Запчасть не найдена в каталоге')

    def test_cs35_does_not_match_cs35_plus_legacy_card(self):
        _product(
            title='Фильтр Changan CS35 Plus',
            article='CS35PLUS-ONLY',
            brand=self.changan,
            category=self.filters,
            compatibility=(
                'Changan CS35 Plus 1.6 JL478QEP. '
                'CS35 без Plus этим номером не подтверждается.'
            ),
        )

        response = self._get(self.changan, self.cs35)

        self.assertEqual(self._ids(response), [])
        self.assertFalse(response.context['legacy_model_fallback'])

    def test_cs35_plus_positive_legacy_card_is_found(self):
        legacy = _product(
            title='Фильтр Changan CS35 Plus',
            article='CS35PLUS-POS',
            brand=self.changan,
            category=self.filters,
            compatibility='Changan CS35 Plus 1.6 JL478QEP.',
        )

        response = self._get(self.changan, self.cs35plus)

        self.assertEqual(self._ids(response), [legacy.pk])
        self.assertTrue(response.context['legacy_model_fallback'])

    def test_fallback_does_not_cross_brand(self):
        _product(
            title='Toyota товар с текстом Tiggo 7',
            article='WRONG-BRAND-T7',
            brand=self.toyota,
            category=self.filters,
            compatibility='Tiggo 7',
        )

        response = self._get(self.chery, self.tiggo7)

        self.assertEqual(self._ids(response), [])
        self.assertFalse(response.context['legacy_model_fallback'])

    def test_fallback_respects_query_category_and_city(self):
        wanted = _product(
            title='Уникальный салонный фильтр Tiggo 7 NEEDLE',
            article='FIT-NEEDLE',
            brand=self.chery,
            category=self.filters,
            city='Алматы',
            compatibility='Chery Tiggo 7',
        )
        _product(
            title='Уникальный салонный фильтр Tiggo 7 NEEDLE',
            article='FIT-WRONG-CAT',
            brand=self.chery,
            category=self.suspension,
            city='Алматы',
            compatibility='Chery Tiggo 7',
        )
        _product(
            title='Уникальный салонный фильтр Tiggo 7 NEEDLE',
            article='FIT-WRONG-CITY',
            brand=self.chery,
            category=self.filters,
            city='Астана',
            compatibility='Chery Tiggo 7',
        )
        _product(
            title='Другой фильтр Tiggo 7',
            article='FIT-WRONG-QUERY',
            brand=self.chery,
            category=self.filters,
            city='Алматы',
            compatibility='Chery Tiggo 7',
        )

        response = self._get(
            self.chery,
            self.tiggo7,
            q='NEEDLE',
            category=str(self.filters.pk),
            city='Алматы',
        )

        self.assertEqual(self._ids(response), [wanted.pk])
        self.assertTrue(response.context['legacy_model_fallback'])
