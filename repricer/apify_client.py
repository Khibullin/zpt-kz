"""Small, bounded Apify API client. Tokens never enter URLs or error text."""
import json
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

ACTOR_ID = 'HnPPZa2uxfQtHeXnx'
ACTOR_PATH = 'firsthand~kaspi-api'
TERMINAL = {'SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'}


class ApifyError(ValueError):
    pass


def configured():
    return bool(getattr(settings, 'APIFY_API_TOKEN', '').strip())


def identifier(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9]{10,64}', value):
        raise ApifyError('Некорректный идентификатор Apify.')
    return value


def request(method, path, *, params=None, payload=None):
    token = getattr(settings, 'APIFY_API_TOKEN', '').strip()
    if not token:
        raise ApifyError('Не настроен ключ подключения Apify на сервере.')
    try:
        with requests.request(method, 'https://api.apify.com/v2/' + path,
                headers={'Authorization': 'Bearer ' + token}, params=params,
                json=payload, timeout=(4, 12), allow_redirects=False, stream=True) as response:
            if response.status_code not in (200, 201):
                raise ApifyError(f'Apify вернул HTTP {response.status_code}. Повторный запуск не выполнен.')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise ApifyError('Ответ Apify превышает 25 МБ.')
                chunks.append(chunk)
            return json.loads(b''.join(chunks))
    except (requests.RequestException, UnicodeError, json.JSONDecodeError):
        raise ApifyError('Нет подтверждённого ответа Apify. Проверьте историю прогонов перед повторным запуском.') from None


def run_info(run_id):
    data = request('GET', f'actor-runs/{identifier(run_id)}').get('data', {})
    if data.get('actId') != ACTOR_ID:
        raise ApifyError('Этот прогон относится к другому Actor, нужен firsthand/kaspi-api.')
    return data


def run_input(info):
    return request('GET', f'key-value-stores/{identifier(info.get("defaultKeyValueStoreId"))}/records/INPUT')


def list_runs(offset=0):
    return request('GET', f'acts/{ACTOR_PATH}/runs',
                   params={'desc': 'true', 'limit': 20, 'offset': offset}).get('data', {})


def budget(value):
    try:
        amount = Decimal(str(value))
        cap = Decimal(str(getattr(settings, 'APIFY_MAX_RUN_USD', '5')))
        if not amount.is_finite() or not cap.is_finite() or amount < Decimal('0.01') or amount > cap:
            raise ValueError
        if amount != amount.quantize(Decimal('0.01')):
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise ApifyError('Укажите лимит расходов от $0.01 до серверного предела, с точностью до цента.') from None


def start_run(payload, max_charge):
    return request('POST', f'acts/{ACTOR_PATH}/runs', payload=payload, params={
        'maxTotalChargeUsd': str(max_charge), 'timeout': 1800,
        'memory': 256, 'restartOnError': 'false', 'waitForFinish': 0,
    }).get('data', {})


def dataset_page(dataset_id, offset):
    data = request('GET', f'datasets/{identifier(dataset_id)}/items', params={
        'format': 'json', 'offset': offset, 'limit': 500,
    })
    if not isinstance(data, list) or len(data) > 500:
        raise ApifyError('Apify вернул некорректную страницу результатов.')
    return data
