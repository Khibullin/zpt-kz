from django.test import SimpleTestCase

from core.services.home_parts_preflight import (
    REJECT_NOT_PARTS,
    REJECT_UNSPECIFIED_PART,
    WARNING_AMBIGUOUS_CATEGORY,
    WARNING_CATEGORY_MISMATCH,
    WARNING_MULTIPLE_CATEGORIES,
    evaluate_home_parts_preflight,
)


class HomePartsPreflightDecisionTests(SimpleTestCase):
    def _decision(self, query, category='Тормоза'):
        return evaluate_home_parts_preflight(
            query=query,
            category=category,
            positions=[query],
        )

    def test_nested_radiator_grille_is_body_not_cooling(self):
        decision = self._decision('решетка радиатора', 'Кузов')
        self.assertIsNone(decision)

    def test_engine_mount_genitive_is_not_rejected(self):
        decision = self._decision('подушка двигателя', 'Тормоза')
        self.assertIsNone(decision)

    def test_steering_wheel_phrase_suggests_salon(self):
        decision = self._decision('рулевое колесо', 'Охлаждение')
        self.assertEqual(decision.code, WARNING_CATEGORY_MISMATCH)
        self.assertEqual(decision.suggested_category, 'Салон')

    def test_bare_wheel_stays_ambiguous(self):
        decision = self._decision('руль', 'Салон')
        self.assertEqual(decision.code, WARNING_AMBIGUOUS_CATEGORY)
        self.assertEqual(decision.suggested_category, '')
        self.assertFalse(decision.is_reject)

    def test_part_with_service_word_is_not_rejected(self):
        decision = self._decision('нужен радиатор и замена', 'Трансмиссия')
        self.assertEqual(decision.code, WARNING_CATEGORY_MISMATCH)
        self.assertEqual(decision.suggested_category, 'Охлаждение')
        self.assertTrue(decision.is_reject)

    def test_car_repair_without_a_part_is_rejected(self):
        decision = self._decision('нужен ремонт автомобиля')
        self.assertTrue(decision.is_reject)
        self.assertEqual(decision.code, REJECT_NOT_PARTS)

    def test_two_categories_do_not_guess_one(self):
        decision = self._decision('колодки, амортизатор', 'Тормоза')
        self.assertEqual(decision.code, WARNING_MULTIPLE_CATEGORIES)
        self.assertEqual(decision.suggested_category, '')

    def test_generic_text_is_rejected_even_if_confirmed_flag_is_irrelevant(self):
        decision = self._decision('помогите')
        self.assertTrue(decision.is_reject)
        self.assertEqual(decision.code, REJECT_UNSPECIFIED_PART)

    def test_broad_words_do_not_force_a_wrong_category(self):
        cases = (
            ('датчик ABS', 'Тормоза', 'Электрика'),
            ('датчик температуры', 'Охлаждение', 'Электрика'),
            ('мотор печки', 'Салон', 'Двигатель'),
            ('клапан АКПП', 'Трансмиссия', 'Двигатель'),
            ('рычаг КПП', 'Трансмиссия', 'Ходовая часть'),
            ('блок управления АКПП', 'Трансмиссия', 'Электрика'),
        )
        for query, category, forbidden in cases:
            with self.subTest(query=query):
                decision = self._decision(query, category)
                self.assertNotEqual(
                    getattr(decision, 'code', ''),
                    WARNING_MULTIPLE_CATEGORIES,
                )
                self.assertNotEqual(
                    getattr(decision, 'suggested_category', ''),
                    forbidden,
                )
                if decision is not None:
                    self.assertEqual(decision.code, WARNING_CATEGORY_MISMATCH)

    def test_exact_article_skips_content_preflight(self):
        self.assertIsNone(self._decision('52119-0K040', 'Трансмиссия'))
        self.assertIsNone(self._decision('M111109111', 'Кузов'))
        self.assertIsNone(self._decision('1109190CR01', 'Охлаждение'))

    def test_glass_variants_require_body_category(self):
        for query in ('Стекло лобовое', 'лобовое стекло', 'нужно лобовое стекло',
                      'стекло ветровое', 'замена лобового стекла', 'стекло задней двери'):
            with self.subTest(query=query):
                decision = self._decision(query, 'Топливная система')
                self.assertTrue(decision.is_reject)
                self.assertEqual(decision.suggested_category, 'Кузов')
                self.assertIsNone(self._decision(query, 'Кузов'))

    def test_known_garbage_is_rejected_but_unlisted_parts_are_allowed(self):
        for query in ('абракадабра', 'qwerty', 'нужна фыва'):
            self.assertTrue(self._decision(query).is_reject)
        self.assertIsNone(self._decision('синхронизатор третьей передачи', 'Трансмиссия'))

    def test_longer_interior_phrase_wins_over_body_plural(self):
        self.assertIsNone(self._decision('обшивка двери', 'Салон'))
