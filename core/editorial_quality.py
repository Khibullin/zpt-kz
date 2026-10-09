"""Conservative editorial release gate; AI and seller data alone are not evidence."""
import re
from django.core.exceptions import ValidationError

from core.models import EditorialPage

VIN_OR_FITMENT = re.compile(r'(?i)(совместим|подходит|применим|подойдёт|устанавливается на|\bOEM\b|кросс.?номер)')
SOURCE_URL = re.compile(r'https://[^\s<>"]+', re.I)
DISCLAIMER = re.compile(r'(?i)(проверьте|сверьте|уточните|не подтвержда)')
MIN_WORDS = 90


def editorial_release_errors(page, *, evidence_confirmed=False):
    """Evidence attestation is required for catalog-based technical pages.

    Automatic text checks cannot verify fitment or OEM cross references.
    """
    errors = []
    if len(page.body.split()) < MIN_WORDS:
        errors.append('Статья слишком короткая для публикации')
    if not page.title.strip() or not page.meta_description.strip():
        errors.append('Отсутствует заголовок или SEO-описание')
    if page.source_candidate_id:
        if not evidence_confirmed:
            errors.append('Нет подтверждения редактора по независимым источникам')
        if not DISCLAIMER.search(page.body):
            errors.append('Нет инструкции по проверке совместимости')
        if VIN_OR_FITMENT.search(page.body) and not SOURCE_URL.search(page.body):
            errors.append('Для технических утверждений не указан независимый источник')
    return errors


def approve_editorial_page(page, *, evidence_confirmed=False):
    if page.status == EditorialPage.STATUS_PUBLISHED:
        return page
    if page.status not in (EditorialPage.STATUS_DRAFT, EditorialPage.STATUS_REVIEW):
        raise ValidationError('Недопустимый статус')
    errors = editorial_release_errors(page, evidence_confirmed=evidence_confirmed)
    if errors:
        raise ValidationError(errors)
    page.status = EditorialPage.STATUS_PUBLISHED
    page.save(update_fields=['status','published_at','updated_at'])
    return page


def instagram_caption_for_article(page):
    """Prepare copy only. Does not call Instagram APIs or publish."""
    if page.status != EditorialPage.STATUS_PUBLISHED:
        raise ValidationError('Instagram-анонс допустим только для опубликованной статьи')
    url = f'https://zpt.kz/guide/parts/{page.slug}/'
    return (
        f'{page.title}\n\n'
        'Подбираете запчасть? Сверяйте артикул, параметры и совместимость '
        'с документацией автомобиля. На ZPT.KZ — полезная инструкция '
        'и предложения продавцов.\n\n'
        f'Подробнее: {url}\n'
        '#автозапчасти #Казахстан #ZPTKZ'
    )
