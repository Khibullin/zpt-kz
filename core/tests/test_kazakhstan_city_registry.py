from django.test import SimpleTestCase

from core.kazakhstan_locations import (
    FIRST_CIRCLE_CITIES,
    FIRST_CIRCLE_SELLER_SATURATION_TARGET,
    KAZAKHSTAN_CITIES,
    canonical_kazakhstan_city,
    canonicalize_kazakhstan_cities,
)


class KazakhstanCityRegistryTests(SimpleTestCase):
    def test_first_circle_contains_exactly_19_priority_cities(self):
        self.assertEqual(len(FIRST_CIRCLE_CITIES), 19)
        self.assertIn('Алматы', FIRST_CIRCLE_CITIES)
        self.assertIn('Астана', FIRST_CIRCLE_CITIES)
        self.assertIn('Конаев', FIRST_CIRCLE_CITIES)
        self.assertIn('Жезказган', FIRST_CIRCLE_CITIES)
        self.assertIn('Усть-Каменогорск', FIRST_CIRCLE_CITIES)
        self.assertNotIn('Шымкент', FIRST_CIRCLE_CITIES)
        self.assertEqual(FIRST_CIRCLE_SELLER_SATURATION_TARGET, 30)
        self.assertTrue(set(FIRST_CIRCLE_CITIES).issubset(set(KAZAKHSTAN_CITIES)))

    def test_explicit_aliases_are_canonicalized(self):
        cases = {
            'Almaty': 'Алматы',
            'Astana': 'Астана',
            'Taraz': 'Тараз',
            'Oskemen': 'Усть-Каменогорск',
            'Өскемен': 'Усть-Каменогорск',
            'Qonaev': 'Конаев',
            'Uralsk': 'Уральск',
        }
        for raw, expected in cases.items():
            self.assertEqual(canonical_kazakhstan_city(raw), expected)

    def test_unknown_city_is_not_fuzzy_matched(self):
        self.assertIsNone(canonical_kazakhstan_city('Город-которого-нет'))

    def test_city_list_is_canonicalized_and_deduplicated(self):
        self.assertEqual(
            canonicalize_kazakhstan_cities(
                ['Almaty', ' Алматы ', 'Astana', 'Астана', 'Taraz', 'unknown']
            ),
            ['Алматы', 'Астана', 'Тараз'],
        )
