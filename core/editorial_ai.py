"""Opt-in AI improvement of existing draft. Never publishes content."""
import json
import re

import requests
from django.conf import settings
from django.db import transaction

from core.models import EditorialPage

FORBIDDEN = re.compile(r'(?i)(гарантированно подходит|точно совместим|100% совместим)')
MAX_INPUT = 2500
MAX_OUTPUT = 4500


def improve_draft_with_ai(page_id: int, *, session=None):
    """Rewrite draft only; require editor to review facts before publishing.

    The editable result is intentionally not automatically promoted even to review.
    """
    if not getattr(settings, 'EDITORIAL_AI_ENABLED', False):
        raise ValueError('Генерация ИИ отключена')
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        raise ValueError('Ключ ИИ не настроен')
    page = EditorialPage.objects.select_related('source_candidate').get(pk=page_id)
    if page.status != EditorialPage.STATUS_DRAFT:
        raise ValueError('Можно улучшать только черновики')
    if not page.source_candidate_id:
        raise ValueError('Нет проверяемого источника темы')
    product = page.source_candidate.source_product
    if product.status != 'active' or not product.article:
        raise ValueError('Исходный товар недоступен')
    factual_data = {
        'name': product.title[:255],
        'seller_article': product.article[:100],
        'seller_description_unverified': (product.description or '')[:MAX_INPUT],
    }
    instructions = (
        'Ты редактор автозапчастей. Перепиши фактический черновик по-русски, '
        'просто, содержательно, без рекламных обещаний. Используй только входные данные. '
        'Описание продавца не является подтверждением применяемости. '
        'Не утверждай совместимость, оригинальность, наличие, цену, качество или OEM-кросс '
        'без независимых подтверждений. Не придумывай номера деталей, модели или двигатели. '
        'Добавь инструкцию проверки применяемости и обращение к продавцу. '
        'Верни исключительно текст статьи без HTML и markdown, не более 4500 символов.'
    )
    client = session or requests
    response = client.post(
        'https://api.openai.com/v1/responses',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'model': getattr(settings, 'EDITORIAL_AI_MODEL', 'gpt-5.6-luna'),
            'instructions': instructions,
            'input': json.dumps(factual_data, ensure_ascii=False),
            'max_output_tokens': 1300,
            'store': False,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    chunks = [
        segment.get('text', '')
        for output in data.get('output', [])
        for segment in output.get('content', [])
        if segment.get('type') == 'output_text'
    ]
    new_body = '\n'.join(chunks).strip()
    if not 150 <= len(new_body) <= MAX_OUTPUT:
        raise ValueError('ИИ вернул материал неподходящего объёма')
    if FORBIDDEN.search(new_body):
        raise ValueError('ИИ выдал неподтверждённые утверждения')
    # Recheck state under lock to prevent an in-flight call overwriting approved content.
    with transaction.atomic():
        locked = EditorialPage.objects.select_for_update().get(pk=page_id)
        if locked.status != EditorialPage.STATUS_DRAFT or locked.body != page.body:
            raise ValueError('Черновик изменился во время генерации')
        locked.body = new_body
        locked.save(update_fields=['body', 'updated_at'])
    return locked
