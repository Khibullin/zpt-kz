"""One-shot visual transcription of manually pasted Kaspi screenshots.

The model does not verify identity, price, OEM fitment, or product availability;
exact brand/article matches may fill the comparison automatically;
ambiguous or incomplete identity remains for manual review.
"""
import json
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
FIELDS = ('name', 'brand', 'article', 'category', 'description', 'specs',
          'fitment', 'package', 'oem', 'analogs', 'screenshot_price', 'screenshot_product_id')
TEXT_LIMITS = {
    'name': 350, 'brand': 120, 'article': 120, 'category': 200,
    'description': 1600, 'specs': 1600, 'fitment': 1500,
    'package': 250, 'oem': 400, 'analogs': 500,
    'screenshot_price': 80, 'screenshot_product_id': 80,
}


def recognize_screenshots(images, *, article, brand, session=None):
    if not getattr(settings, 'OPENAI_API_KEY', ''):
        raise ValueError('Распознавание недоступно: не задан ключ OpenAI на сервере.')
    if not images:
        raise ValueError('Сначала вставьте скриншот Kaspi.')
    prompt = (
        'Ты распознаёшь только ВИДИМЫЕ данные на скриншотах страницы Kaspi.kz. '
        'Ничего не выдумывай и не добавляй сведения по памяти или из запроса. '
        'Отделяй реальные характеристики от рекламных блоков. '
        'Цена на скриншоте НЕ обязательно минимальная цена среди продавцов. В screenshot_price укажи одну полную цену товара в тенге, не ежемесячный платёж рассрочки. '
        'Это скриншот для товара поставщика: бренд ' + brand[:120] +
        ', артикул ' + article[:120] +
        '. Указанные бренд/артикул используй только для проверки, а не как источник заполнения. '
        'Верни только JSON объект с полями: name, brand, article, category, description, '
        'specs, fitment, package, oem, analogs, screenshot_price, screenshot_product_id (код товара Kaspi) и product_photo_box. '
        'Текстовые значения — строки. Для невидимых или неуверенно распознанных сведений оставь пустую строку. Не угадывай бренд, полный артикул, цену и код товара. Если скриншоты содержат разные товары или противоречащие цены/коды, оставь brand и article пустыми для ручной проверки. '
        'product_photo_box — либо null, либо массив из пяти чисел '
        '[номер_скриншота_с_нуля, левая_граница, верхняя_граница, правая_граница, нижняя_граница]. '
        'Координаты нормализованы от 0 до 1000 по ширине и высоте ПОЛНОГО скриншота. '
        'Выбери прямоугольник главного ФОТО ТОВАРА Kaspi (не скриншот страницы целиком). '
        'Он должен охватывать только сам товар и его фон, исключать цены, кнопки, '
        'название магазина, характеристики и рекламные блоки. '
        'Если фотографию детали нельзя уверенно выделить, верни null. '
        'В fitment перечисли марки, модели и годы только если показаны; '
        'в package укажи число в комплекте только если видно. '
        'В specs не придумывай детали. Дословность важнее полноты. '
        'Не повторяй текст, не соответствующий товару.'
    )
    content = [{'type': 'input_text', 'text': prompt}]
    content.extend({'type': 'input_image', 'image_url': image, 'detail': 'high'}
                   for image in images[:3])
    client = session or requests
    try:
        response = client.post(
            'https://api.openai.com/v1/responses',
            headers={'Authorization': 'Bearer ' + settings.OPENAI_API_KEY,
                     'Content-Type': 'application/json'},
            json={'model': getattr(settings, 'PRODUCT_AI_MODEL', 'gpt-5.6-luna'),
                  'input': [{'role': 'user', 'content': content}],
                  'max_output_tokens': 1200, 'store': False},
            timeout=45)
        response.raise_for_status()
        payload = response.json()
        output = str(payload.get('output_text') or '')
        if not output:
            output = '\n'.join(
                block.get('text', '')
                for item in payload.get('output', [])
                for block in item.get('content', [])
                if block.get('type') == 'output_text')
        start, end = output.find('{'), output.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('Ответ распознавания не содержит данных.')
        data = json.loads(output[start:end + 1])
        if not isinstance(data, dict):
            raise ValueError('Некорректный ответ распознавания.')
        result = {
            field: str(data.get(field) or '').strip()[:TEXT_LIMITS[field]]
            for field in FIELDS
        }
        from .comparison_photo_crop import normalize_box
        result['product_photo_box'] = normalize_box(
            data.get('product_photo_box'), len(images[:3]))
        return result
    except (requests.RequestException, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        logger.warning('Kaspi screenshot OCR failed: %s', str(exc)[:200])
        raise ValueError('Распознавание не выполнено. Проверьте доступ к сервису и повторите.') from exc


def article_agrees(extracted, expected):
    # Punctuation/case are meaningful for supplier identity.
    return str(extracted or '').strip().casefold() == str(expected or '').strip().casefold()


def price_from_image(value):
    """Keep the literal observed amount alongside the separately validated calculation."""
    clean = str(value or '').strip()[:80]
    if not clean or not re.search(r'\d', clean):
        return ''
    return clean


def parsed_screenshot_price(value):
    """Accept one positive tenge amount; reject instalments and ambiguous text."""
    from decimal import Decimal
    text = str(value or '').strip().replace('\u00a0', ' ').replace('\u202f', ' ')
    text = re.sub(r'\s*(?:₸|тенге|KZT|тг\.?)\s*$', '', text, flags=re.I).strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d{1,2})?', text):
        raise ValueError('Укажите одну полную цену в тенге, без рассрочки и пояснений.')
    price = Decimal(text.replace(' ', '').replace(',', '.'))
    if not 0 < price < Decimal('1000000000000'):
        raise ValueError('Цена должна быть больше нуля и меньше 1 трлн тенге.')
    return price.quantize(Decimal('.01'))


def screenshot_price_applies(material, audit):
    if not material or not audit or not isinstance(material.kaspi_data, dict):
        return False
    data = material.kaspi_data
    try:
        return bool(data.get('price_applied') and
                    parsed_screenshot_price(data.get('screenshot_price')) == audit.min_price)
    except ValueError:
        return False
