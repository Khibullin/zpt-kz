from django.test import TestCase

from catalog.image_generator import build_publication_caption, generate_instagram_feed, generate_instagram_story
from catalog.instagram_card import InstagramCardContent, render_instagram_card
from core.instagram_sanitize import build_public_location_line
from core.models import Request


def _red_rows(image, y0, y1):
    rows = []
    for y in range(y0, y1):
        for x in range(0, image.width, 4):
            red, green, blue = image.getpixel((x, y))
            if red > 180 and green < 90 and blue < 90:
                rows.append(y)
                break
    return rows


def _ink_rows(image, y0, y1):
    rows = []
    for y in range(max(0, y0), min(image.height, y1)):
        for x in range(36, image.width - 36, 6):
            red, green, blue = image.getpixel((x, y))
            if red < 245 or green < 245 or blue < 245:
                rows.append(y)
                break
    return rows


def _dark_pixel_count(image, y0, y1):
    count = 0
    for y in range(max(0, y0), min(image.height, y1)):
        for x in range(48, 760, 2):
            red, green, blue = image.getpixel((x, y))
            if red < 40 and green < 50 and blue < 60:
                count += 1
    return count


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

    def test_long_part_keeps_brand_and_headline_inside_profile_crop(self):
        content = InstagramCardContent(
            vehicle='Hyundai Sonata',
            part='Передний левый блок управления климат-контролем в сборе с панелью и проводкой',
            location='Астана',
            request_number='№ 482',
        )
        feed = render_instagram_card(content, kind='feed')
        story = render_instagram_card(content, kind='story')
        crop = (feed.image.height - feed.image.width) // 2
        self.assertEqual(_ink_rows(feed.image, 0, crop), [])
        self.assertEqual(_ink_rows(feed.image, feed.image.height - crop, feed.image.height), [])
        safe_red = _red_rows(feed.image, crop, crop + 220)
        self.assertTrue(safe_red)
        self.assertGreaterEqual(safe_red[0], crop + 24)
        self.assertGreater(_dark_pixel_count(feed.image, safe_red[0] + 36, safe_red[0] + 130), 40)
        self.assertGreater(feed.part_size, feed.headline_size)
        self.assertGreaterEqual(feed.part_size, 32)
        self.assertEqual(_ink_rows(story.image, 0, 240), [])
        self.assertTrue(_red_rows(story.image, 250, 430))
        self.assertGreater(story.part_size, story.headline_size)

    def test_very_long_part_shrinks_without_covering_brand(self):
        content = InstagramCardContent(
            vehicle='Mercedes-Benz GLE Coupe 400 d 4MATIC 2019',
            part='Передний левый блок управления климат-контролем ' * 8,
            location='Караганда • По Казахстану',
            request_number='№ 483',
        )
        feed = render_instagram_card(content, kind='feed')
        crop = (feed.image.height - feed.image.width) // 2
        brand_rows = _red_rows(feed.image, crop, crop + 220)
        self.assertTrue(brand_rows)
        self.assertEqual(_ink_rows(feed.image, 0, crop), [])
        self.assertEqual(_ink_rows(feed.image, feed.image.height - crop, feed.image.height), [])
        self.assertGreater(feed.part_size, feed.headline_size)

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
