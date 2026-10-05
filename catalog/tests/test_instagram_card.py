from django.test import TestCase

from catalog.image_generator import build_publication_caption, generate_instagram_feed, generate_instagram_story
from catalog.instagram_card import InstagramCardContent, render_instagram_card
from core.instagram_sanitize import build_public_location_line
from core.models import Request


class PublicLocationTests(TestCase):
    def test_kazakhstan_scope_is_explicit(self):
        self.assertEqual(
            build_public_location_line(search_scope='kazakhstan', city='Шымкент'),
            'Шымкент • По Казахстану',
        )
        self.assertEqual(
            build_public_location_line(search_scope='kazakhstan', city=''),
            'По Казахстану',
        )

    def test_city_scope_does_not_invent_countrywide_search(self):
        self.assertEqual(
            build_public_location_line(search_scope='city', city='Алматы'),
            'Алматы',
        )
        self.assertEqual(
            build_public_location_line(search_scope='city', city=''),
            '',
        )

    def test_custom_cities_stay_real(self):
        self.assertEqual(
            build_public_location_line(
                search_scope='custom',
                city='Астана',
                selected_cities='Алматы, Караганда',
            ),
            'Астана • Алматы, Караганда',
        )


class InstagramCardLayoutTests(TestCase):
    def test_feed_and_story_sizes_keep_part_larger_than_headline(self):
        content = InstagramCardContent(
            vehicle='Lexus ES 350',
            part='Блок климат-контроля',
            location='Шымкент • По Казахстану',
            request_number='№ 1042',
        )
        feed = render_instagram_card(content, kind='feed')
        story = render_instagram_card(content, kind='story')
        self.assertEqual(feed.image.size, (1080, 1350))
        self.assertEqual(story.image.size, (1080, 1920))
        self.assertGreater(feed.vehicle_size, feed.headline_size)
        self.assertGreater(feed.part_size, feed.headline_size)
        self.assertGreater(story.vehicle_size, story.headline_size)
        self.assertGreater(story.part_size, story.headline_size)

    def test_long_part_name_stays_readable_and_does_not_invent_fields(self):
        content = InstagramCardContent(
            vehicle='Mercedes-Benz GLE Coupe 400 d 4MATIC',
            part='Передний левый блок управления климат-контролем в сборе с панелью',
            location='',
            request_number='',
        )
        rendered = render_instagram_card(content, kind='feed')
        self.assertGreater(rendered.part_size, rendered.headline_size)
        self.assertGreaterEqual(rendered.part_size, 32)
        self.assertEqual(rendered.image.size, (1080, 1350))

    def test_saved_story_and_feed_use_real_request_data_only(self):
        request = Request.objects.create(
            brand='Toyota',
            model='Camry',
            year=2016,
            category='Тормоза',
            description='Колодки, звоните 77001112233, VIN JTDBR32E720012345',
            city='Алматы',
            search_scope='city',
            phone='77001112233',
            vin='JTDBR32E720012345',
            status='sent',
        )
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                _story, caption = generate_instagram_story(request)
                feed_path, feed_caption = generate_instagram_feed(request)
        self.assertEqual(caption, feed_caption)
        self.assertIn('Покупатель ищет:', caption)
        self.assertIn('2016', caption)
        self.assertIn('Город: Алматы', caption)
        self.assertIn(f'Заявка №{request.pk}', caption)
        self.assertNotIn('77001112233', caption)
        self.assertNotIn('JTDBR32E720012345', caption)
        self.assertNotIn(str(request.access_token), caption)
        if request.short_token:
            self.assertNotIn(request.short_token, caption)
        self.assertNotIn('Откройте заявку', caption)
        self.assertTrue(feed_path.name.startswith(f'feed_{request.pk}_'))
        self.assertNotIn(str(request.access_token), feed_path.name)

    def test_caption_omits_empty_city_and_does_not_promise_opening_request(self):
        request = Request.objects.create(
            brand='Kia',
            model='Rio',
            category='Фара',
            city='',
            search_scope='city',
            phone='77001112233',
            status='sent',
        )
        caption = build_publication_caption(request)
        self.assertNotIn('Город:', caption)
        self.assertNotIn('Казахстан', caption)
        self.assertIn('Продавцы ZPT.KZ получают заявки в WhatsApp.', caption)
        self.assertIn('Переход на сайт — по ссылке в профиле', caption)
