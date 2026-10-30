import re
from types import SimpleNamespace
from django.test import SimpleTestCase
from catalog.product_localization import product_title_translations, kazakh_product_title
from catalog.templatetags.product_extras import kazakh_product_seo_name


class KazakhProductTitleTests(SimpleTestCase):
    def test_snapshot_preserves_codes_dimensions_and_pack_sizes(self):
        titles = product_title_translations()
        self.assertGreaterEqual(len(titles), 240)
        for original, translated in titles.items():
            with self.subTest(title=original):
                self.assertEqual(re.findall(r'\d+(?:[.,]\d+)*', original), re.findall(r'\d+(?:[.,]\d+)*', translated))
                identifiers = lambda text: [word for word in re.findall(r'[A-Za-z][A-Za-z0-9_.-]*', text) if word not in ('R', 'L', 'c')]
                self.assertEqual(identifiers(original), identifiers(translated))

    def test_unknown_title_is_not_guessed(self):
        self.assertEqual(kazakh_product_title('Новая неизвестная деталь OEM ABC-123'), '')
        product = SimpleNamespace(title='Новая деталь', article='ABC-123')
        self.assertEqual(kazakh_product_seo_name(product), 'Новая деталь ABC-123')

    def test_article_added_once_and_html_left_for_template_escaping(self):
        product = SimpleNamespace(title='Воздушный фильтр Changan UNI-K', article='ABC-123')
        self.assertEqual(kazakh_product_seo_name(product), 'Ауа сүзгісі Changan UNI-K ABC-123')
        product = SimpleNamespace(title='Амортизатор задний R газомасляный ESCE 51945', article='51945')
        translated = kazakh_product_seo_name(product)
        self.assertEqual(translated.count('51945'), 1)
        self.assertIn('оң жақ', translated)
