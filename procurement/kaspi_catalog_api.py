"""Kaspi's documented catalogue import API. Never fabricate publication success."""
import json
import requests
from django.conf import settings
from django.core import signing
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

BASE = 'https://kaspi.kz/shop/api/products/'


class CatalogError(ValueError):
    pass


class CatalogUncertain(CatalogError):
    pass


def connected():
    return bool(getattr(settings, 'KASPI_API_TOKEN', '').strip())


class CatalogClient:
    def call(self, path, method='GET', params=None, payload=None):
        if not connected():
            raise CatalogError('Не подключён API-ключ магазина Kaspi. Ручное размещение доступно.')
        try:
            response = requests.request(method, BASE+path, params=params,
                headers={'Accept':'application/json', 'Content-Type':'text/plain',
                    'X-Auth-Token':settings.KASPI_API_TOKEN},
                data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
                timeout=(4,15), allow_redirects=False)
        except requests.RequestException:
            if method == 'POST':
                raise CatalogUncertain('Ответ Kaspi не получен. Результат отправки неизвестен; повторная отправка заблокирована до проверки в кабинете Kaspi.')
            raise CatalogError('Не удалось получить ответ Kaspi. Повторите проверку позже.')
        if response.status_code not in (200,201,202):
            if method == 'POST' and response.status_code >= 500:
                raise CatalogUncertain('Kaspi вернул серверную ошибку. Результат отправки неизвестен; проверьте кабинет Kaspi перед повтором.')
            raise CatalogError(f'API Kaspi: HTTP {response.status_code}. Проверьте подключение и права ключа.')
        try:
            return response.json()
        except ValueError:
            if method == 'POST':
                raise CatalogUncertain('Kaspi принял запрос, но ответ не распознан. Повторная отправка заблокирована до проверки.')
            raise CatalogError('Kaspi вернул нечитаемый ответ.')

    def categories(self):
        return self.call('classification/categories')

    def attributes(self, category):
        return self.call('classification/attributes', params={'c':category})

    def submit(self, material):
        result = self.call('import', method='POST', payload=[material])
        if not isinstance(result,dict) or not result.get('code'):
            raise CatalogUncertain('Kaspi не вернул код загрузки. Требуется проверка в кабинете Kaspi.')
        return result

    def status(self, code):
        return self.call('import',params={'i':code})

    def result(self, code):
        return self.call('import/result',params={'i':code})


def signed_image_url(asset):
    token = signing.dumps({'image':asset.pk,'digest':asset.digest},salt='kaspi-published-photo',compress=True)
    base = str(getattr(settings,'PUBLIC_BASE_URL','') or 'https://zpt.kz').rstrip('/')
    return base+reverse('procurement:kaspi_shared_image',args=[token])


@require_GET
def shared_image(request, token):
    from .models import KaspiCardImage
    try:
        data = signing.loads(token,salt='kaspi-published-photo',max_age=7*86400)
        asset = KaspiCardImage.objects.only('data').get(pk=data['image'],digest=data['digest'])
    except (signing.BadSignature, KeyError, ValueError, TypeError, KaspiCardImage.DoesNotExist):
        raise Http404
    response = HttpResponse(bytes(asset.data),content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
