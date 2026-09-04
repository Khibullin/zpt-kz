import os
from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from PIL import Image

from catalog.forms import ProductForm, SellerProfileForm, SellerRegisterForm
from catalog.image_upload_policy import MAX_UPLOAD_BYTES, optimize_uploaded_image
from catalog.models import SellerProfile
from catalog.product_image_upload import (
    TOO_MANY_IMAGES_MESSAGE,
    build_product_image_plan,
)
from catalog.remote_image import RemoteImageError

PRODUCT_FORM_DATA = {
    'title': 'Тест фото',
    'article': 'IMG-001',
    'price': '1500',
    'condition': 'new',
    'status': 'active',
}

SELLER_REGISTER_DATA = {
    'name': 'Photo Market',
    'phone': '77771110001',
    'password': 'secret12345',
    'city': 'Алматы',
    'address': 'ул. Тестовая, 1',
    'pickup_same_as_store': True,
    'pickup_available': True,
    'pickup_address': '',
    'work_hours': '',
    'delivery_info': '',
}

SELLER_PROFILE_DATA = {
    'name': 'Edit Photo Market',
    'phone': '77770001122',
    'city': 'Алматы',
    'address': 'ул. Профиля, 10',
    'pickup_same_as_store': True,
    'pickup_available': True,
    'pickup_address': '',
    'work_hours': '',
    'delivery_info': '',
    'instagram': '',
    'website': '',
    'description': '',
}


def _jpeg_bytes(width, height, color=(210, 40, 40)):
    buffer = BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, format='JPEG', quality=90)
    return buffer.getvalue()


def _png_rgba_bytes(width=40, height=40):
    image = Image.new('RGBA', (width, height), (255, 0, 0, 0))
    image.putpixel((8, 8), (0, 255, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _gif_bytes():
    buffer = BytesIO()
    Image.new('RGB', (12, 12), (0, 0, 255)).save(buffer, format='GIF')
    return buffer.getvalue()


def _jpeg_over_limit():
    width = 2000
    while width <= 3600:
        image = Image.frombytes(
            'RGB',
            (width, width),
            os.urandom(width * width * 3),
        )
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=100)
        data = buffer.getvalue()
        if len(data) > MAX_UPLOAD_BYTES:
            return data
        width += 400
    raise RuntimeError('Could not build JPEG larger than 5 MiB')


def _open_image(uploaded):
    uploaded.seek(0)
    return Image.open(uploaded)


class OptimizeUploadedImageTests(TestCase):
    def test_large_jpeg_is_resized_to_webp(self):
        uploaded = SimpleUploadedFile(
            'wide-photo.jpg',
            _jpeg_bytes(2400, 1200),
            content_type='image/jpeg',
        )
        result = optimize_uploaded_image(uploaded)
        self.assertTrue(result.name.endswith('.webp'))
        with _open_image(result) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, (1600, 800))

    def test_small_jpeg_is_not_upscaled(self):
        uploaded = SimpleUploadedFile(
            'small.jpg',
            _jpeg_bytes(800, 600),
            content_type='image/jpeg',
        )
        result = optimize_uploaded_image(uploaded)
        with _open_image(result) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, (800, 600))

    def test_png_transparency_is_preserved_in_webp(self):
        uploaded = SimpleUploadedFile(
            'logo.png',
            _png_rgba_bytes(),
            content_type='image/png',
        )
        result = optimize_uploaded_image(uploaded)
        self.assertTrue(result.name.endswith('.webp'))
        with _open_image(result) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.mode, 'RGBA')
            self.assertLess(image.getpixel((0, 0))[3], 10)
            self.assertGreater(image.getpixel((8, 8))[3], 245)

    def test_file_over_5_mib_is_rejected(self):
        uploaded = SimpleUploadedFile(
            'huge.jpg',
            b'x' * (MAX_UPLOAD_BYTES + 1),
            content_type='image/jpeg',
        )
        with self.assertRaises(ValidationError) as ctx:
            optimize_uploaded_image(uploaded)
        self.assertIn('5 МБ', str(ctx.exception))

    def test_gif_is_rejected_even_when_renamed_jpg(self):
        uploaded = SimpleUploadedFile(
            'photo.jpg',
            _gif_bytes(),
            content_type='image/jpeg',
        )
        with self.assertRaises(ValidationError) as ctx:
            optimize_uploaded_image(uploaded)
        self.assertIn('JPEG, PNG и WebP', str(ctx.exception))

    def test_broken_jpeg_is_rejected(self):
        uploaded = SimpleUploadedFile(
            'broken.jpg',
            b'not-an-image',
            content_type='image/jpeg',
        )
        with self.assertRaises(ValidationError) as ctx:
            optimize_uploaded_image(uploaded)
        self.assertIn('корректным', str(ctx.exception))

    def test_existing_fieldfile_is_returned_unchanged(self):
        existing = SimpleNamespace(name='already-saved.jpg', size=123)
        self.assertIs(optimize_uploaded_image(existing), existing)

        original = ContentFile(_jpeg_bytes(800, 600), name='kept.jpg')
        self.assertIs(optimize_uploaded_image(original), original)
        self.assertIsNone(optimize_uploaded_image(None))


class ProductFormImageUploadTests(TestCase):
    def test_new_main_image_is_optimized_to_webp(self):
        uploaded = SimpleUploadedFile(
            'main.jpg',
            _jpeg_bytes(2000, 1000),
            content_type='image/jpeg',
        )
        form = ProductForm(data=PRODUCT_FORM_DATA, files={'main_image': uploaded})
        self.assertTrue(form.is_valid(), form.errors)
        main_image = form.cleaned_data['main_image']
        self.assertTrue(main_image.name.endswith('.webp'))
        with _open_image(main_image) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(max(image.size), 1600)
            self.assertEqual(image.size, (1600, 800))

    def test_main_image_over_5_mib_is_invalid(self):
        uploaded = SimpleUploadedFile(
            'huge.jpg',
            _jpeg_over_limit(),
            content_type='image/jpeg',
        )
        form = ProductForm(data=PRODUCT_FORM_DATA, files={'main_image': uploaded})
        self.assertFalse(form.is_valid())
        self.assertIn('main_image', form.errors)
        self.assertIn('5 МБ', str(form.errors['main_image']))


class AdditionalProductImageUploadTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_local_extra_jpeg_is_optimized_to_webp(self):
        uploaded = SimpleUploadedFile(
            'extra.jpg',
            _jpeg_bytes(2000, 1000),
            content_type='image/jpeg',
        )
        request = self.factory.post('/', data={'extra_images': uploaded})
        plan = build_product_image_plan(request)
        self.assertEqual(len(plan.extra_local), 1)
        extra = plan.extra_local[0]
        self.assertTrue(extra.name.endswith('.webp'))
        with _open_image(extra) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(max(image.size), 1600)
            self.assertEqual(image.size, (1600, 800))

    def test_extra_over_5_mib_raises_remote_image_error(self):
        uploaded = SimpleUploadedFile(
            'huge-extra.jpg',
            b'x' * (MAX_UPLOAD_BYTES + 1),
            content_type='image/jpeg',
        )
        request = self.factory.post('/', data={'extra_images': uploaded})
        with self.assertRaises(RemoteImageError) as ctx:
            build_product_image_plan(request)
        self.assertIn('5 МБ', str(ctx.exception))

    def test_more_than_four_additional_keeps_existing_message(self):
        extras = [
            SimpleUploadedFile(
                f'extra-{index}.jpg',
                _jpeg_bytes(20, 20),
                content_type='image/jpeg',
            )
            for index in range(5)
        ]
        request = self.factory.post('/', data={'extra_images': extras})
        with self.assertRaises(RemoteImageError) as ctx:
            build_product_image_plan(request)
        self.assertEqual(str(ctx.exception), TOO_MANY_IMAGES_MESSAGE)


class SellerLogoUploadTests(TestCase):
    def test_register_form_logo_is_optimized_to_webp(self):
        uploaded = SimpleUploadedFile(
            'shop-logo.jpg',
            _jpeg_bytes(2000, 2000),
            content_type='image/jpeg',
        )
        form = SellerRegisterForm(
            data=SELLER_REGISTER_DATA,
            files={'logo': uploaded},
        )
        self.assertTrue(form.is_valid(), form.errors)
        logo = form.cleaned_data['logo']
        self.assertTrue(logo.name.endswith('.webp'))
        with _open_image(logo) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(max(image.size), 1600)
            self.assertEqual(image.size, (1600, 1600))

    def test_profile_form_keeps_existing_logo_without_new_upload(self):
        user = User.objects.create_user(
            username='77770001122',
            password='secret12345',
        )
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                seller = SellerProfile(
                    user=user,
                    name='Edit Photo Market',
                    phone='77770001122',
                    city='Алматы',
                    address='ул. Профиля, 10',
                )
                seller.logo.save(
                    'old-logo.png',
                    ContentFile(_png_rgba_bytes()),
                    save=True,
                )
                old_name = seller.logo.name
                form = SellerProfileForm(data=SELLER_PROFILE_DATA, instance=seller)
                self.assertTrue(form.is_valid(), form.errors)
                logo = form.cleaned_data['logo']
                self.assertEqual(logo.name, old_name)
                self.assertFalse(str(logo.name).endswith('.webp'))
