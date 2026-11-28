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
        'Текстовые значения — строки. Для невидимых или неуверенно распознанных сведений оставь пустую строку. Не угадывай бренд, полный артикул, цену и код товара. Не объединяй артикул производителя и OEM: в article должен быть только полный артикул детали, оригинальные номера записывай отдельно в oem. Если скриншоты содержат разные товары или противоречащие цены/коды, оставь brand и article пустыми для ручной проверки. '
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
        'Артикул может быть виден в названии товара: перенеси его в article, даже если отдельного поля нет. '
        'Буквы L и R различают левую и правую детали: никогда не заменяй их артикулом поставщика. '
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
    # Owner-approved: spaces and dash typography are formatting only.
    # Keep every letter/digit, leading zero, slash, dot and L/R suffix.
    def normalized(value):
        return re.sub(r'[\s\-\u2010-\u2015\u2212]+', '', str(value or '')).casefold()
    observed, supplier = normalized(extracted), normalized(expected)
    return bool(observed and supplier and observed == supplier)


def manual_article_agrees(extracted, expected):
    """A manager may confirm a multi-number card containing the complete SKU.

    Only explicit manual confirmation uses this. Match whole tokens, allowing
    spaces inside an article; never accept an expected SKU as a substring.
    """
    if article_agrees(extracted, expected):
        return True
    parts = [part for part in re.split(r'[\s;,|]+', str(extracted or '').strip()) if part]
    for start in range(len(parts)):
        for end in range(start + 1, len(parts) + 1):
            if article_agrees(' '.join(parts[start:end]), expected):
                return True
    return False


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


def separate_article_oem(fields, expected):
    """Remove only separately corroborated numeric OEM tokens from an OCR article.

    Full article punctuation stays meaningful. Additional article variants and
    uncorroborated suffixes cannot turn a mismatch into a match.
    """
    article = str(fields.get('article') or '').strip()
    expected = str(expected or '').strip()
    if not expected or article_agrees(article, expected) or re.search(r'\s', expected):
        return fields
    tokens = [part for part in re.split(r'[\s;,]+', article) if part]
    if sum(article_agrees(part, expected) for part in tokens) != 1:
        return fields
    extras = [part for part in tokens if not article_agrees(part, expected)]
    oem_tokens = set(re.split(r'[\s;,]+', str(fields.get('oem') or '').strip()))
    if not extras or not all(part.isascii() and part.isdecimal() and len(part) >= 5
                             and part in oem_tokens for part in extras):
        return fields
    return {**fields, 'article': expected}


def screenshot_article_fields(fields):
    """Recover a single literal part number from the title, never from the offer.

    Numeric OEMs/years are not manufacturer articles. Multiple plausible numbers
    remain unresolved. Explicit OCR identity (including a mismatch) wins.
    """
    if str(fields.get('article') or '').strip():
        return fields
    tokens = re.findall(r'(?<![\w./-])[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*(?![\w./-])',
                        str(fields.get('name') or ''))
    candidates = {token for token in tokens if re.search(r'\d', token)
                  and (re.search(r'[A-Za-z]', token) or re.search(r'[./-]', token))}
    oem_tokens = {token.casefold() for token in re.split(r'[\s;,]+', str(fields.get('oem') or ''))}
    candidates = {token for token in candidates if token.casefold() not in oem_tokens
                  and token.casefold() != str(fields.get('brand') or '').casefold()}
    if len(candidates) == 1:
        return {**fields, 'article': candidates.pop()}
    return fields
