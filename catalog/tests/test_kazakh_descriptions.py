import hashlib
import re
from collections import Counter
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from catalog.product_description import readable_description
from catalog.product_localization import description_translations, kazakh_description_summary


class KazakhDescriptionTests(SimpleTestCase):
    def test_snapshot_preserves_every_number_and_technical_identifier(self):
        entries = description_translations()
        self.assertEqual(len(entries), 243)
        numbers = lambda text: Counter(re.findall(r'\d+(?:[.,]\d+)*', text))
        identifier = re.compile(r'\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b')
        for digest, entry in entries.items():
            with self.subTest(source=digest):
                ru, kk = entry['ru_summary'], entry['kk_summary']
                self.assertTrue(ru)
                self.assertTrue(kk)
                self.assertEqual(numbers(ru), numbers(kk))
                self.assertEqual(Counter(identifier.findall(ru)), Counter(identifier.findall(kk)))

    def test_changed_supplier_description_never_uses_old_translation(self):
        source = 'Фильтр OEM ABC-123.\n\nХарактеристики поставщика: Модель: Camry'
        summary = 'Фильтр OEM ABC-123.'
        digest = hashlib.md5(source.encode(), usedforsecurity=False).hexdigest()
        entry = {'ru_summary': summary, 'kk_summary': 'OEM ABC-123 сүзгісі.'}
        with patch('catalog.product_localization.description_translations', return_value={digest: entry}):
            result = readable_description(source)
            self.assertEqual(result['summary_kk'], entry['kk_summary'])
            self.assertEqual(result['original'], source)
            self.assertEqual(kazakh_description_summary(source + ' изменено', summary), '')
            self.assertEqual(kazakh_description_summary(source, 'Другое описание'), '')

    def test_short_description_keeps_applicability_and_exclusions_visible(self):
        source = 'Фильтр ABC-123.\nПрименимость: Chery A3. Не для Jetour X70.\nПроверьте VIN.'
        result = readable_description(source)
        self.assertEqual(result['summary'], source)
        self.assertEqual(result['original'], source)
        self.assertEqual(result['summary_kk'], '')

    def test_both_languages_are_escaped_and_original_stays_separate(self):
        unsafe = '\"><img src=x onerror=alert(1)>'
        content = dict(original=unsafe, summary=unsafe, summary_kk=unsafe, facts=[], compatibility=[])
        html = render_to_string('catalog/includes/product_readable_description.html', {'description_content': content})
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)
        self.assertIn('description-summary-bilingual', html)
        self.assertIn('data-no-translate', html)

    def test_translation_preserves_negative_fitment_warning(self):
        entries = description_translations().values()
        item = next(e for e in entries if e['ru_summary'].startswith('Воздушный фильтр M111109111.'))
        self.assertIn('Не для Jetour X70', item['ru_summary'])
        self.assertIn('Jetour X70 үшін емес', item['kk_summary'])

    def test_glued_legacy_dimensions_gain_line_breaks_without_changing_source(self):
        source = 'Технические характеристикиМатериал: TPU.Геометрические размеры:Малый внутренний диаметр: 28 ммБольшой внутренний диаметр: 83 ммВысота пыльника: 115 мм'
        result = readable_description(source)
        self.assertEqual(result['original'], source)
        self.assertEqual(result['summary'], source)
        self.assertIn('\nМатериал:', result['summary_display'])
        self.assertIn('\nБольшой внутренний диаметр', result['summary_display'])
        self.assertIn('\nВысота пыльника:', result['summary_display'])
