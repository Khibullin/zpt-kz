from django.test import TestCase

from catalog.instagram_public_text import (
    REASON_AMBIGUOUS_PART,
    REASON_AMBIGUOUS_SIDE,
    REASON_AMBIGUOUS_VEHICLE,
    REASON_PII_DETECTED,
    REVIEW_REASON_CODES,
    build_banner_v2_caption,
    build_public_part_request,
)
from catalog.instagram_banner_v2 import (
    PART_BODY,
    PART_BRAKES,
    PART_ELECTRICAL,
    PART_ENGINE,
    PART_FILTER,
    PART_GENERIC,
    PART_HEADLIGHT,
    PART_SUSPENSION,
    PART_TRANSMISSION,
    PART_WHEEL,
    BannerContent,
    fit_text_to_width,
    part_category,
    render_request_banner_v2,
)
from PIL import ImageDraw
from core.models import Request


def _request(**overrides):
    payload = {
        'brand': 'Haval',
        'model': 'Dargo',
        'year': 2023,
        'category': '',
        'description': 'фара правая и левая на хавал дарго желательно оригинал',
        'city': 'Алматы',
        'phone': '77001112233',
        'status': 'sent',
    }
    payload.update(overrides)
    return Request.objects.create(**payload)


class PublicTextNormalizationTests(TestCase):
    def test_haval_headlights_are_structured_without_rewriting_raw_text(self):
        raw = 'фара правая и левая на хавал дарго желательно оригинал'
        product_request = _request(description=raw)
        public = build_public_part_request(product_request)
        product_request.refresh_from_db()

        self.assertEqual(product_request.description, raw)
        self.assertEqual(public.brand, 'Haval')
        self.assertEqual(public.model, 'Dargo')
        self.assertEqual(public.year, '2023')
        self.assertEqual(public.city, 'Алматы')
        self.assertEqual(public.public_request_number, str(product_request.pk))
        self.assertEqual(
            public.parts,
            ['Правая передняя фара', 'Левая передняя фара'],
        )
        self.assertEqual(public.notes, ['Желательно оригинал'])
        self.assertEqual(public.review_reason, '')

    def test_side_without_part_requires_review_and_does_not_invent_a_part(self):
        product_request = _request(
            description='нужен правый передний',
            year=None,
        )
        public = build_public_part_request(product_request)

        self.assertEqual(public.parts, [])
        self.assertEqual(public.year, '')
        self.assertEqual(public.review_reason, REASON_AMBIGUOUS_PART)

    def test_or_side_is_not_guessed(self):
        product_request = _request(description='фара правая или левая')
        public = build_public_part_request(product_request)
        self.assertEqual(public.parts, [])
        self.assertEqual(public.review_reason, REASON_AMBIGUOUS_SIDE)

    def test_missing_vehicle_requires_review(self):
        product_request = _request(brand='', model='', description='передние колодки')
        public = build_public_part_request(product_request)
        self.assertEqual(public.review_reason, REASON_AMBIGUOUS_VEHICLE)
        self.assertEqual(public.parts, ['Передние колодки'])

    def test_phone_in_a_public_field_is_not_published(self):
        product_request = _request(city='77001112233', description='передние колодки')
        public = build_public_part_request(product_request)
        self.assertEqual(public.review_reason, REASON_PII_DETECTED)
        self.assertEqual(public.city, '')
        caption = build_banner_v2_caption(public)
        self.assertNotIn('77001112233', caption)
        self.assertNotIn('WhatsApp', caption)
        self.assertNotIn('wa.me', caption)

    def test_caption_uses_normalized_parts_and_profile_link(self):
        product_request = _request()
        public = build_public_part_request(product_request)
        caption = build_banner_v2_caption(public)
        self.assertIn('Haval Dargo, 2023', caption)
        self.assertIn('📍 Алматы', caption)
        self.assertIn('• правая передняя фара;', caption)
        self.assertIn('• левая передняя фара.', caption)
        self.assertIn('Получайте заявки покупателей на ZPT.KZ.', caption)
        self.assertIn('ссылка в профиле', caption)
        self.assertIn('#Haval', caption)
        self.assertIn('#HavalDargo', caption)
        self.assertNotIn('хавал дарго желательно', caption)
        self.assertNotIn(product_request.phone, caption)


def _is_red(pixel) -> bool:
    red, green, blue = pixel
    return red > 180 and green < 90 and blue < 90


def _is_blue(pixel) -> bool:
    red, green, blue = pixel
    return blue > 140 and red < 80 and green < 140


def _is_dark(pixel) -> bool:
    return pixel[0] < 40 and pixel[1] < 45 and pixel[2] < 50


def _is_light(pixel) -> bool:
    return pixel[0] > 220 and pixel[1] > 220 and pixel[2] > 220


class BannerV2LayoutTests(TestCase):
    def _haval(self, **overrides):
        payload = dict(
            brand='Haval',
            model='Dargo',
            year='2023',
            parts=('Правая передняя фара', 'Левая передняя фара'),
            city='Алматы',
            request_number='486',
        )
        payload.update(overrides)
        return BannerContent(**payload)

    def _render(self, **overrides):
        return render_request_banner_v2(self._haval(**overrides))

    def test_banner_matches_the_approved_frame(self):
        rendered = self._render()
        image = rendered.image
        self.assertEqual(image.size, (1080, 1350))
        self.assertLess(rendered.content_bottom, image.height - 180)
        footer = image.getpixel((40, image.height - 24))
        self.assertTrue(_is_dark(footer))
        logo_red = any(
            _is_red(image.getpixel((x, y)))
            for y in range(36, 90, 2)
            for x in range(70, 220, 4)
        )
        badge_red = any(
            _is_red(image.getpixel((x, y)))
            for y in range(40, 130, 2)
            for x in range(760, 1000, 4)
        )
        self.assertTrue(logo_red)
        self.assertTrue(badge_red)
        paired_rows = [
            y for y in range(880, 1140, 2)
            if _is_red(image.getpixel((180, y))) and _is_light(image.getpixel((860, y)))
        ]
        self.assertTrue(paired_rows)
        buyer_has_blue = any(
            _is_blue(image.getpixel((x, y)))
            for y in paired_rows[::4]
            for x in range(600, 1020, 2)
        )
        self.assertTrue(buyer_has_blue)
        red = blue = 0
        sampled = 0
        for y in range(0, image.height, 4):
            for x in range(0, image.width, 4):
                pixel = image.getpixel((x, y))
                sampled += 1
                if _is_red(pixel):
                    red += 1
                if _is_blue(pixel):
                    blue += 1
        self.assertGreater(red, blue)
        self.assertLess(red / sampled, 0.22)
        self.assertLess(blue, 2500)

    def test_long_names_shrink_and_stay_inside_the_safe_area(self):
        probe = ImageDraw.Draw(self._render().image)
        short = fit_text_to_width(probe, 'HAVAL', 960, max_size=72, min_size=36)
        long = fit_text_to_width(probe, 'LAND CRUISER PRADO', 960, max_size=72, min_size=36)
        self.assertGreaterEqual(getattr(short, 'size', 72), getattr(long, 'size', 36))
        cases = (
            self._haval(brand='Honda', model='CR-V', year='2007', city='Шымкент', parts=(
                'Правая передняя дверь',
                'Правое переднее крыло',
            )),
            self._haval(brand='Chery', model='Tiggo 7 Pro', year='2022'),
            self._haval(brand='Mercedes-Benz', model='GLE', year='2019'),
            self._haval(
                brand='Toyota',
                model='Land Cruiser Prado',
                year='2021',
                parts=(
                    'Передний правый нижний рычаг подвески',
                    'Электронный блок управления двигателем',
                ),
            ),
            self._haval(parts=(
                'Передний левый блок управления климат-контролем в сборе с панелью и проводкой',
            )),
        )
        for content in cases:
            image = render_request_banner_v2(content).image
            footer_top = image.height - 204
            for y in range(0, footer_top, 10):
                self.assertTrue(_is_light(image.getpixel((image.width - 8, y))))
                self.assertTrue(_is_light(image.getpixel((8, y))))

    def test_changing_copy_changes_the_banner(self):
        base = render_request_banner_v2(self._haval()).image
        other_number = render_request_banner_v2(self._haval(request_number='999')).image
        other_city = render_request_banner_v2(self._haval(city='Астана')).image
        other_year = render_request_banner_v2(self._haval(year='2021')).image
        self.assertNotEqual(list(base.getdata()), list(other_number.getdata()))
        self.assertNotEqual(list(base.getdata()), list(other_city.getdata()))
        self.assertNotEqual(list(base.getdata()), list(other_year.getdata()))

    def test_part_counts_change_the_list_and_keep_the_footer(self):
        one = self._render(parts=('Правая передняя фара',)).image
        two = self._render().image
        three = self._render(parts=(
            'Правая передняя фара',
            'Левая передняя фара',
            'Капот',
        )).image
        more = self._render(
            parts=('Правая передняя фара', 'Левая передняя фара', 'Капот', 'Радиатор'),
            hidden_part_count=2,
        ).image
        self.assertNotEqual(list(one.getdata()), list(two.getdata()))
        self.assertNotEqual(list(two.getdata()), list(three.getdata()))
        self.assertNotEqual(list(three.getdata()), list(more.getdata()))
        self.assertTrue(_is_dark(more.getpixel((20, more.height - 12))))

    def test_more_than_three_parts_keep_the_footer_clear(self):
        rendered = render_request_banner_v2(
            BannerContent(
                brand='Toyota',
                model='Camry',
                year='2018',
                parts=('Передние колодки', 'Капот', 'Радиатор', 'Скрытая деталь'),
                hidden_part_count=2,
                city='Караганда',
                request_number='99999',
            )
        )
        without_extra = render_request_banner_v2(
            BannerContent(
                brand='Toyota',
                model='Camry',
                year='2018',
                parts=('Передние колодки', 'Капот', 'Радиатор'),
                city='Караганда',
                request_number='99999',
            )
        )
        image = rendered.image
        footer = image.getpixel((20, image.height - 10))
        self.assertLess(footer[0], 40)
        self.assertLess(rendered.content_bottom, image.height - 80)
        self.assertNotEqual(list(image.getdata()), list(without_extra.image.getdata()))

    def test_long_model_and_part_names_stay_inside_the_canvas(self):
        rendered = render_request_banner_v2(
            BannerContent(
                brand='Mercedes-Benz',
                model='GLE Coupe 400 d 4MATIC',
                year='2018-2020',
                parts=(
                    'Передний левый блок управления климат-контролем в сборе с панелью и проводкой',
                ),
                city='Алматы',
                request_number='486',
            )
        )
        image = rendered.image
        footer_top = image.height - 204
        edge = [image.getpixel((image.width - 8, y)) for y in range(0, footer_top, 8)]
        self.assertTrue(all(_is_light(pixel) for pixel in edge))
        footer = image.getpixel((20, image.height - 10))
        self.assertLess(footer[0], 40)

    def test_part_icons_are_categories_not_specific_parts(self):
        self.assertEqual(part_category('Правая передняя фара'), PART_HEADLIGHT)
        self.assertEqual(part_category('Капот'), PART_BODY)
        self.assertEqual(part_category('Радиатор'), PART_ENGINE)
        self.assertEqual(part_category('Передние колодки'), PART_BRAKES)
        self.assertEqual(part_category('Амортизатор'), PART_SUSPENSION)
        self.assertEqual(part_category('Масляный фильтр'), PART_FILTER)
        self.assertEqual(part_category('Генератор'), PART_ELECTRICAL)
        self.assertEqual(part_category('АКПП'), PART_TRANSMISSION)
        self.assertEqual(part_category('Литой диск'), PART_WHEEL)
        self.assertEqual(part_category('Правая передняя дверь'), PART_BODY)
        self.assertEqual(part_category('Правое переднее крыло'), PART_BODY)
        self.assertEqual(part_category('Передний правый нижний рычаг подвески'), PART_SUSPENSION)
        self.assertEqual(part_category('Электронный блок управления двигателем'), PART_ENGINE)
        self.assertEqual(part_category('Непонятная позиция'), PART_GENERIC)
        import inspect
        import catalog.instagram_banner_v2 as banner_module
        source = inspect.getsource(banner_module).casefold()
        self.assertNotIn('wa.me', source)
        self.assertNotIn('whatsapp', source)
        self.assertNotIn('доставк', source)
        self.assertNotIn('silhouette', source)
        self.assertNotIn('vehiclevisual', source)
        headlight = render_request_banner_v2(self._haval(parts=('Правая передняя фара',))).image
        generic = render_request_banner_v2(self._haval(parts=('Непонятная позиция',))).image
        self.assertNotEqual(list(headlight.getdata()), list(generic.getdata()))

    def test_missing_images_are_not_a_review_reason(self):
        blocked = {
            'vehicle_image_required',
            'vehicle_image_low_confidence',
            'part_image_low_confidence',
        }
        self.assertFalse(blocked.intersection(REVIEW_REASON_CODES))
        product_request = _request()
        public = build_public_part_request(product_request)
        self.assertEqual(public.review_reason, '')
