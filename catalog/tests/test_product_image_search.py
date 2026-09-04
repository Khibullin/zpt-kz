import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductImage, SellerProfile
from catalog.product_image_search import (
    MAX_RESULTS,
    PHOTO_WARNING,
    parse_brave_image_results,
    search_product_images,
)
from catalog.product_image_upload import (
    TOO_MANY_IMAGES_MESSAGE,
    parse_remote_image_tokens,
)
from catalog.remote_image import (
    MAX_IMAGE_BYTES,
    RemoteImageBlockedError,
    RemoteImageError,
    assert_public_http_url,
    download_public_image,
    read_remote_image_token,
    sign_remote_image_token,
)


MINIMAL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    b'\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\x0d\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _seller(username='photo-seller', name='Photo Shop', phone='77770000999'):
    user = User.objects.create_user(username=username, password='secret12345')
    return SellerProfile.objects.create(
        user=user,
        name=name,
        phone=phone,
        city='Алматы',
    )


def _product(**kwargs):
    defaults = {
        'title': 'Фильтр воздушный',
        'price': 2000,
        'seller_name': 'Photo Shop',
        'whatsapp_number': '77770000999',
        'status': 'active',
        'article': 'AF-900',
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def _brave_item(index, *, article_in_title=False):
    article = 'AF-900'
    title = f'{article} air filter' if article_in_title else f'Random part {index}'
    return {
        'title': title,
        'url': f'https://source.example/{index}',
        'source': f'shop{index}.example',
        'description': article if article_in_title else 'other',
        'thumbnail': {'src': f'https://thumbs.example/{index}.jpg'},
        'properties': {'url': f'https://cdn.example/{index}.jpg'},
    }


class BraveImageParseTests(TestCase):
    def test_parses_brave_image_result_and_caps_at_six(self):
        payload = {
            'results': [
                _brave_item(0, article_in_title=False),
                _brave_item(1, article_in_title=True),
                _brave_item(2, article_in_title=False),
                _brave_item(3, article_in_title=False),
                _brave_item(4, article_in_title=True),
                _brave_item(5, article_in_title=False),
                _brave_item(6, article_in_title=False),
                _brave_item(7, article_in_title=False),
            ]
        }
        parsed = parse_brave_image_results(payload, 'AF-900')
        self.assertEqual(len(parsed), MAX_RESULTS)
        self.assertTrue(parsed[0].title.startswith('AF-900'))
        self.assertEqual(parsed[0].image_url, 'https://cdn.example/1.jpg')
        self.assertEqual(parsed[0].thumbnail_url, 'https://thumbs.example/1.jpg')
        self.assertEqual(parsed[0].source_url, 'https://source.example/1')
        public = parsed[0].to_public_dict()
        self.assertIn('token', public)
        self.assertNotIn('image_url', public)
        self.assertEqual(public['confidence'], 'high')

    def test_skips_results_without_original_image_url(self):
        payload = {
            'results': [
                {
                    'title': 'AF-900',
                    'url': 'https://source.example/x',
                    'thumbnail': {'src': 'https://thumbs.example/x.jpg'},
                    'properties': {},
                }
            ]
        }
        self.assertEqual(parse_brave_image_results(payload, 'AF-900'), [])


class ProductImageSearchEndpointTests(TestCase):
    def setUp(self):
        self.seller = _seller()
        self.url = reverse('ajax_product_image_search')
        self.product = _product(
            seller_profile=self.seller,
            seller_name=self.seller.name,
            slug='af-900-photo',
        )

    def test_guest_cannot_search_photos(self):
        before = bool(self.product.main_image)
        response = self.client.post(self.url, {'article': 'AF-900'})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(bool(self.product.main_image), before)

    def test_photo_search_does_not_change_product(self):
        self.client.login(username='photo-seller', password='secret12345')
        before = Product.objects.get(pk=self.product.pk)
        fake_payload = {'results': [_brave_item(1, article_in_title=True)]}
        with override_settings(BRAVE_SEARCH_API_KEY='test-brave-key'):
            with patch(
                'catalog.product_image_search.fetch_brave_image_payload',
                return_value=fake_payload,
            ) as mocked:
                response = self.client.post(
                    self.url,
                    data=json.dumps({'article': 'AF-900', 'title': 'Фильтр'}),
                    content_type='application/json',
                )
        mocked.assert_called_once()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['warning'], PHOTO_WARNING)
        self.assertEqual(len(payload['images']), 1)
        self.assertIn('token', payload['images'][0])
        self.product.refresh_from_db()
        self.assertEqual(self.product.title, before.title)
        self.assertEqual(self.product.main_image.name, before.main_image.name)
        self.assertEqual(Product.objects.count(), 1)

    def test_search_helper_does_not_use_network_when_urlopen_injected(self):
        calls = []

        class FakeResponse:
            status = 200
            headers = {'Content-Type': 'application/json'}

            def read(self):
                return json.dumps({'results': [_brave_item(1, article_in_title=True)]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(req, timeout=None):
            calls.append(req.full_url)
            return FakeResponse()

        result = search_product_images(
            'AF-900',
            title='Фильтр',
            api_key='test-brave-key',
            urlopen=fake_open,
        )
        self.assertTrue(result['ok'])
        self.assertEqual(len(calls), 1)
        self.assertIn('images/search', calls[0])
        self.assertIn('safesearch=strict', calls[0])
        self.assertIn('country=ALL', calls[0])


class RemoteImageSecurityTests(TestCase):
    def test_signed_photo_token_roundtrip(self):
        token = sign_remote_image_token(
            image_url='https://cdn.example.com/part.jpg',
            thumbnail_url='https://thumbs.example.com/part.jpg',
            source_url='https://source.example.com/part',
            title='AF-900',
        )
        data = read_remote_image_token(token)
        self.assertEqual(data['image_url'], 'https://cdn.example.com/part.jpg')
        self.assertEqual(data['source_url'], 'https://source.example.com/part')
        with self.assertRaises(RemoteImageError):
            read_remote_image_token(token + 'tampered')

    def test_rejects_private_and_localhost_urls(self):
        blocked = [
            'http://127.0.0.1/secret.jpg',
            'http://localhost/secret.jpg',
            'http://192.168.0.10/a.jpg',
            'http://10.0.0.8/a.jpg',
            'http://169.254.169.254/latest/meta-data',
            'http://[::1]/a.jpg',
            'file:///etc/passwd',
            'ftp://example.com/a.jpg',
        ]
        for url in blocked:
            with self.assertRaises(RemoteImageBlockedError):
                assert_public_http_url(url)

    def test_redirect_to_private_ip_is_rejected(self):
        def resolver(hostname):
            if hostname == 'cdn.example.com':
                return [(2, 1, 0, '', ('93.184.216.34', 0))]
            raise AssertionError(f'unexpected host {hostname}')

        def fake_open(req, timeout=None):
            raise HTTPError(
                req.full_url,
                302,
                'Found',
                {'Location': 'http://127.0.0.1/hidden.jpg'},
                None,
            )

        with self.assertRaises(RemoteImageBlockedError):
            download_public_image(
                'https://cdn.example.com/part.jpg',
                urlopen=fake_open,
                resolver=resolver,
            )

    def test_oversized_image_rejected(self):
        def resolver(hostname):
            return [(2, 1, 0, '', ('93.184.216.34', 0))]

        class FakeResponse:
            status = 200
            headers = {'Content-Length': str(MAX_IMAGE_BYTES + 10)}

            def read(self, n=-1):
                return b'x' * 100

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(req, timeout=None):
            return FakeResponse()

        with self.assertRaises(RemoteImageError) as ctx:
            download_public_image(
                'https://cdn.example.com/huge.jpg',
                urlopen=fake_open,
                resolver=resolver,
            )
        self.assertIn('5 МБ', str(ctx.exception))

    def test_invalid_image_rejected(self):
        def resolver(hostname):
            return [(2, 1, 0, '', ('93.184.216.34', 0))]

        class FakeResponse:
            status = 200
            headers = {'Content-Type': 'text/html'}

            def read(self, n=-1):
                return b'<html>not an image</html>'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(req, timeout=None):
            return FakeResponse()

        with self.assertRaises(RemoteImageError):
            download_public_image(
                'https://cdn.example.com/page.html',
                urlopen=fake_open,
                resolver=resolver,
            )

    def test_existing_main_image_not_replaced_without_explicit_choice(self):
        seller = _seller('keep-photo', 'Keep Photo', '77770000888')
        product = _product(
            seller_profile=seller,
            seller_name=seller.name,
            slug='keep-main-image',
            article='KEEP-1',
        )
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                product.main_image.save('keep-old.png', ContentFile(MINIMAL_PNG), save=True)
                old_name = product.main_image.name
                self.client.login(username='keep-photo', password='secret12345')
                response = self.client.post(
                    reverse('edit_product', kwargs={'pk': product.pk}),
                    data={
                        'title': 'Фильтр воздушный обновлённый',
                        'article': 'KEEP-1',
                        'price': '2500',
                        'condition': 'new',
                        'status': 'active',
                    },
                )
                self.assertEqual(response.status_code, 302)
                product.refresh_from_db()
                self.assertEqual(product.title, 'Фильтр воздушный обновлённый')
                self.assertEqual(product.main_image.name, old_name)

    def test_invalid_token_does_not_lose_existing_card(self):
        seller = _seller('token-photo', 'Token Photo', '77770000777')
        product = _product(
            seller_profile=seller,
            seller_name=seller.name,
            slug='token-main-image',
            article='TOKEN-1',
        )
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                product.main_image.save('token-old.png', ContentFile(MINIMAL_PNG), save=True)
                old_name = product.main_image.name
                self.client.login(username='token-photo', password='secret12345')
                response = self.client.post(
                    reverse('edit_product', kwargs={'pk': product.pk}),
                    data={
                        'title': product.title,
                        'article': 'TOKEN-1',
                        'price': '2000',
                        'condition': 'new',
                        'status': 'active',
                        'remote_main_image_token': 'not-a-token',
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Некорректный выбор фото')
                product.refresh_from_db()
                self.assertEqual(product.main_image.name, old_name)

    def test_signed_token_downloads_on_save_and_ignores_client_url(self):
        seller = _seller('import-photo', 'Import Photo', '77770000666')
        logged_in = self.client.login(username='import-photo', password='secret12345')
        self.assertTrue(logged_in)
        token = sign_remote_image_token(image_url='https://cdn.example.com/ok.png')
        seen_urls = []

        def fake_download(url, **kwargs):
            seen_urls.append(url)
            return MINIMAL_PNG, 'PNG'

        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch('catalog.remote_image.download_public_image', side_effect=fake_download):
                    response = self.client.post(
                        reverse('add_product'),
                        data={
                            'title': 'Импортированный фильтр',
                            'article': 'IMP-1',
                            'price': '3000',
                            'condition': 'new',
                            'status': 'active',
                            'remote_main_image_token': token,
                            'remote_image_url': 'http://127.0.0.1/evil.png',
                        },
                    )
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='IMP-1')
                self.assertEqual(product.seller_profile_id, seller.pk)
                self.assertTrue(product.main_image)
                self.assertEqual(seen_urls, ['https://cdn.example.com/ok.png'])
                self.assertEqual(urlparse(seen_urls[0]).hostname, 'cdn.example.com')


class MultiRemoteProductPhotoTests(TestCase):
    def setUp(self):
        self.seller = _seller('multi-photo', 'Multi Photo', '77770000555')
        self.client.login(username='multi-photo', password='secret12345')

    def _token(self, index):
        return sign_remote_image_token(image_url=f'https://cdn.example.com/p{index}.png')

    def _post_add(self, data):
        payload = {
            'title': 'Мультифото фильтр',
            'article': data.pop('article', 'MULTI-1'),
            'price': '3000',
            'condition': 'new',
            'status': 'active',
        }
        payload.update(data)
        return self.client.post(reverse('add_product'), data=payload)

    def test_parse_tokens_dedupes_and_rejects_sixth(self):
        tokens = [self._token(i) for i in range(6)]
        parsed, main = parse_remote_image_tokens({
            'remote_main_image_token': tokens[0],
            'remote_image_tokens': json.dumps(tokens[:5]),
        })
        self.assertEqual(main, tokens[0])
        self.assertEqual(len(parsed), 5)
        with self.assertRaises(RemoteImageError) as ctx:
            parse_remote_image_tokens({
                'remote_main_image_token': tokens[0],
                'remote_image_tokens': json.dumps(tokens),
            })
        self.assertIn('5 фото', str(ctx.exception))
        duped, _ = parse_remote_image_tokens({
            'remote_main_image_token': tokens[1],
            'remote_image_tokens': json.dumps([tokens[1], tokens[1], tokens[2]]),
        })
        self.assertEqual(duped, [tokens[1], tokens[2]])

    def test_one_remote_token_becomes_main(self):
        token = self._token(1)
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self._post_add({
                        'article': 'MULTI-ONE',
                        'remote_main_image_token': token,
                    })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-ONE')
                self.assertTrue(product.main_image)
                self.assertEqual(product.images.count(), 0)

    def test_two_remote_selected_make_main_and_product_image(self):
        tokens = [self._token(1), self._token(2)]
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self._post_add({
                        'article': 'MULTI-TWO',
                        'remote_main_image_token': tokens[0],
                        'remote_image_tokens': json.dumps(tokens),
                    })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-TWO')
                self.assertTrue(product.main_image)
                self.assertEqual(product.images.count(), 1)

    def test_five_remote_selected_make_one_main_and_four_extras(self):
        tokens = [self._token(i) for i in range(5)]
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self._post_add({
                        'article': 'MULTI-FIVE',
                        'remote_main_image_token': tokens[0],
                        'remote_image_tokens': json.dumps(tokens),
                    })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-FIVE')
                self.assertTrue(product.main_image)
                self.assertEqual(product.images.count(), 4)

    def test_sixth_remote_is_rejected(self):
        tokens = [self._token(i) for i in range(6)]
        before = Product.objects.count()
        response = self._post_add({
            'article': 'MULTI-SIX',
            'remote_main_image_token': tokens[0],
            'remote_image_tokens': json.dumps(tokens),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, TOO_MANY_IMAGES_MESSAGE)
        self.assertEqual(Product.objects.count(), before)

    def test_second_selected_can_be_main(self):
        tokens = [self._token(1), self._token(2)]
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self._post_add({
                        'article': 'MULTI-MAIN2',
                        'remote_main_image_token': tokens[1],
                        'remote_image_tokens': json.dumps(tokens),
                    })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-MAIN2')
                self.assertTrue(product.main_image)
                self.assertEqual(product.images.count(), 1)
                self.assertNotEqual(product.main_image.name, product.images.first().image.name)

    def test_duplicate_selection_does_not_create_duplicate_product_image(self):
        token = self._token(8)
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self._post_add({
                        'article': 'MULTI-DUP',
                        'remote_main_image_token': token,
                        'remote_image_tokens': json.dumps([token, token]),
                    })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-DUP')
                self.assertTrue(product.main_image)
                self.assertEqual(product.images.count(), 0)

    def test_invalid_extra_token_does_not_create_product(self):
        token = self._token(1)
        before = Product.objects.count()
        response = self._post_add({
            'article': 'MULTI-BAD',
            'remote_main_image_token': token,
            'remote_image_tokens': json.dumps([token, 'not-a-token']),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Некорректный выбор фото')
        self.assertEqual(Product.objects.count(), before)

    def test_edit_keeps_existing_photos_when_adding_remote_extra(self):
        product = _product(
            seller_profile=self.seller,
            seller_name=self.seller.name,
            slug='keep-gallery',
            article='KEEP-GALLERY',
        )
        extra_token = self._token(3)
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                product.main_image.save('keep-main.png', ContentFile(MINIMAL_PNG), save=True)
                extra = ProductImage.objects.create(
                    product=product,
                    image=ContentFile(MINIMAL_PNG, name='keep-extra.png'),
                )
                old_main = product.main_image.name
                old_extra = extra.image.name
                with patch(
                    'catalog.remote_image.download_public_image',
                    return_value=(MINIMAL_PNG, 'PNG'),
                ):
                    response = self.client.post(
                        reverse('edit_product', kwargs={'pk': product.pk}),
                        data={
                            'title': product.title,
                            'article': 'KEEP-GALLERY',
                            'price': '2000',
                            'condition': 'new',
                            'status': 'active',
                            'remote_image_tokens': json.dumps([extra_token]),
                        },
                    )
                self.assertEqual(response.status_code, 302)
                product.refresh_from_db()
                self.assertEqual(product.main_image.name, old_main)
                self.assertEqual(product.images.count(), 2)
                names = [img.image.name for img in product.images.all()]
                self.assertIn(old_extra, names)

    def test_local_extra_upload_still_works(self):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new('RGB', (8, 8), (10, 20, 30)).save(buffer, format='PNG')
        uploaded = SimpleUploadedFile(
            'extra.png',
            buffer.getvalue(),
            content_type='image/png',
        )
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                response = self._post_add({
                    'article': 'MULTI-LOCAL',
                    'extra_images': uploaded,
                })
                self.assertRedirects(response, reverse('seller_dashboard'))
                product = Product.objects.get(article='MULTI-LOCAL')
                self.assertEqual(product.images.count(), 1)
                self.assertTrue(product.main_image)

    def test_total_max_enforced_with_existing_extras(self):
        product = _product(
            seller_profile=self.seller,
            seller_name=self.seller.name,
            slug='full-gallery',
            article='FULL-GALLERY',
        )
        tokens = [self._token(i) for i in range(4)]
        with TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                product.main_image.save('full-main.png', ContentFile(MINIMAL_PNG), save=True)
                for index in range(4):
                    ProductImage.objects.create(
                        product=product,
                        image=ContentFile(MINIMAL_PNG, name=f'full-extra-{index}.png'),
                    )
                response = self.client.post(
                    reverse('edit_product', kwargs={'pk': product.pk}),
                    data={
                        'title': product.title,
                        'article': 'FULL-GALLERY',
                        'price': '2000',
                        'condition': 'new',
                        'status': 'active',
                        'remote_image_tokens': json.dumps(tokens[:1]),
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, TOO_MANY_IMAGES_MESSAGE)
                product.refresh_from_db()
                self.assertEqual(product.images.count(), 4)
