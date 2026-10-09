"""Prepare factual, unpublished editorial drafts from catalog candidates.

No model API call, invented compatibility, or automated publication.
This is the deterministic and auditable first stage of AI-assisted authoring.
"""
from django.db import transaction
from django.utils.text import slugify

from core.models import EditorialCandidate, EditorialPage


@transaction.atomic
def prepare_editorial_draft(candidate_id: int) -> EditorialPage:
    candidate = EditorialCandidate.objects.select_for_update().select_related('source_product').get(pk=candidate_id)
    if candidate.status == 'rejected':
        raise ValueError('Отклонённая тема не может быть подготовлена')
    product = candidate.source_product
    if product.status != 'active' or not product.article or not product.description:
        raise ValueError('Недостаточно подтверждённых сведений о товаре')
    if EditorialPage.objects.filter(source_candidate=candidate).exists():
        return EditorialPage.objects.get(source_candidate=candidate)

    title = candidate.title
    if not title.strip():
        raise ValueError('Нет названия темы')
    # Unique, stable slug based on source identity instead of unpredictable title collisions.
    slug = f'part-guide-{candidate.pk}'
    if EditorialPage.objects.filter(slug=slug).exists():
        raise ValueError('URL занят другой статьёй')
    text = (
        f'Деталь: {product.title}\n'
        f'Артикул в каталоге продавца: {product.article}\n\n'
        'Что известно по карточке продавца:\n'
        f'{product.description.strip()}\n\n'
        'Что необходимо проверить перед покупкой:\n'
        'Уточните марку, модель, год, двигатель и модификацию автомобиля. '
        'Сверьте артикул и технические параметры по каталогу производителя. '
        'Описание товара само по себе не подтверждает применяемость.\n\n'
        'Если остались сомнения, свяжитесь с продавцом или оставьте заявку на ZPT.KZ.\n\n'
        'Примечание редактору: подтвердите все характеристики и применяемость перед публикацией.'
    )
    page = EditorialPage.objects.create(
        source_candidate=candidate,
        title=title,
        slug=slug,
        seo_title=title[:240],
        meta_description='Информация о детали и рекомендации по проверке артикула и применяемости перед заказом на ZPT.KZ.',
        body=text,
        status=EditorialPage.STATUS_DRAFT,
    )
    page.related_products.add(product)
    candidate.status = 'review'
    candidate.save(update_fields=['status'])
    return page
