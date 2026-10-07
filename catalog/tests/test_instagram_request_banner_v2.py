from django.test import TestCase

from catalog.instagram_public_text import (
    REASON_AMBIGUOUS_PART,
    REASON_AMBIGUOUS_SIDE,
    REASON_AMBIGUOUS_VEHICLE,
    REASON_PII_DETECTED,
    build_banner_v2_caption,
    build_public_part_request,
)
from catalog.instagram_banner_v2 import BannerContent, render_request_banner_v2
from catalog.instagram_visuals import (
    GENERATION_BOUNDARIES,
    VehicleVisualResolver,
)
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


class VehicleVisualResolverTests(TestCase):
    def test_unknown_model_uses_generic_fallback_without_blocking(self):
        asset = VehicleVisualResolver().resolve(brand='Haval', model='Dargo', year=2023)
        self.assertEqual(asset.source_type, 'generic_fallback')
        self.assertFalse(asset.blocks_publish)
        self.assertIsNotNone(asset.image)
        self.assertIn('source_type', asset.provenance())

    def test_generation_boundary_does_not_pick_a_generation(self):
        boundaries = {
            ('haval', 'dargo'): (
                (2022, 2023, 'I'),
                (2023, 2024, 'II'),
            ),
        }
        asset = VehicleVisualResolver().resolve(
            brand='Haval',
            model='Dargo',
            year=2023,
            boundaries=boundaries,
        )
        self.assertTrue(asset.blocks_publish)
        self.assertEqual(asset.review_reason, 'ambiguous_generation')
        self.assertEqual(asset.generation, '')
        self.assertEqual(GENERATION_BOUNDARIES, {})


class BannerV2LayoutTests(TestCase):
    def test_reference_banner_fits_and_keeps_red_secondary(self):
        rendered = render_request_banner_v2(
            BannerContent(
                brand='Haval',
                model='Dargo',
                year='2023',
                parts=('Правая передняя фара', 'Левая передняя фара'),
                city='Алматы',
                request_number='486',
                vehicle_is_generic=True,
            )
        )
        image = rendered.image
        self.assertEqual(image.size, (1080, 1350))
        red = blue = dark_footer = 0
        sampled = 0
        for y in range(0, image.height, 4):
            for x in range(0, image.width, 4):
                r, g, b = image.getpixel((x, y))
                sampled += 1
                if r > 180 and g < 80 and b < 80:
                    red += 1
                if b > 140 and r < 80 and g < 120:
                    blue += 1
                if y > image.height - 100 and r < 40 and g < 40 and b < 45:
                    dark_footer += 1
        self.assertGreater(blue, 20)
        self.assertGreater(dark_footer, 100)
        self.assertLess(red / sampled, 0.2)
        self.assertLess(rendered.content_bottom, image.height - 100)

    def test_many_parts_do_not_cover_the_footer(self):
        rendered = render_request_banner_v2(
            BannerContent(
                brand='Mercedes-Benz',
                model='GLE Coupe 400 d 4MATIC',
                year='2018-2020',
                parts=(
                    'Передний бампер с отверстиями под омыватель и парктроники',
                    'Правая передняя фара в сборе',
                    'Капот',
                    'Левое крыло',
                    'Правое крыло',
                ),
                hidden_part_count=3,
                city='Караганда',
                request_number='99999',
            )
        )
        image = rendered.image
        footer = image.getpixel((20, image.height - 10))
        self.assertLess(footer[0], 40)
        self.assertLess(rendered.content_bottom, image.height - 80)
