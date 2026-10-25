"""New form submissions only: consent and a durable Telegram outbox."""
import logging
import re
from django.db import transaction
from django.utils import timezone
from core.models import Request, RequestConsentEvent, TelegramRequestPublication
from core.services.request_connections import PII, enabled, safe_summary
from core.services.telegram_requests import publish
from core.services.home_parts_category_signals import CATEGORY_PHRASES
from core.services.home_parts_query import looks_like_exact_article

# Fail closed for free text outside the technical vocabulary. This prevents
# publishing names or other identifiers that a phone/URL regex cannot detect.
TECHNICAL_WORDS = set(re.findall(r'\w+', ' '.join(
    phrase for _, phrases in CATEGORY_PHRASES for phrase in phrases)))
TECHNICAL_WORDS.update('датчик датчика датчики abs абс авс abc esp есп sensor нужен нужна нужно нужны ищу куплю купить передний передняя переднее передние задний задняя заднее задние левый левая левое левые правый правая правое правые наружный наружная внутренний внутренняя верхний верхняя нижний нижняя новый новая новое новые бу б у оригинал аналог комплект комплекта шт штуки штука рестайлинг дорестайлинг белый черный синий красный серый цвет в сборе без с на для и или отдельно обе оба по одной два две четыре'.split())


def safe_automatic_description(req):
    summary = ' '.join((req.description or req.article).split())
    if not summary or len(summary) > 500:
        return ''
    if PII.search(summary) or SENSITIVE.search(summary):
        return ''
    if any(value and value in summary for value in
           (req.phone, req.vin, str(req.access_token), req.short_token)):
        return ''
    if looks_like_exact_article(summary):
        return summary
    # A pasted completion can repeat a word without a separator (фараФара).
    # Normalize only the public copy, then apply the same complete vocabulary
    # check; unknown words and personal information still stay on review.
    summary = re.sub(r'(?<=[а-яё])(?=[А-ЯЁ])', ' ', summary)
    summary = re.sub(r'\b(\w+)(?:\s+\1)+\b', r'\1', summary, flags=re.I)
    words = re.findall(r'\w+', summary.casefold().replace('ё', 'е'))
    if any(word not in TECHNICAL_WORDS for word in words):
        return ''
    return summary

VERSION = 'submit_telegram_v1'
logger = logging.getLogger(__name__)
SENSITIVE = re.compile(r'(?i)(?:телефон|адрес|зовут|контакт|почта|улица|ул\.|проспект|instagram|telegram|whatsapp|[\w.+-]+@[\w.-]+)')


@transaction.atomic
def record_submission_consent(req):
    """Called inside the creation transaction, never for a replay or historical row."""
    now = timezone.now()
    req.public_consent_at = now
    req.contact_consent_at = now
    req.save(update_fields=['public_consent_at', 'contact_consent_at'])
    RequestConsentEvent.objects.bulk_create([
        RequestConsentEvent(request=req, purpose=purpose, granted=True,
                            source='request_submit', text_version=VERSION)
        for purpose in ('public', 'contact')
    ])


def process_new_submissions(limit=50):
    if not enabled():
        return 0
    eligible = Request.objects.filter(
        connection_consents__source='request_submit',
        connection_consents__text_version=VERSION,
        connection_consents__purpose='public', connection_consents__granted=True,
        public_consent_at__isnull=False,
    ).exclude(purchase_result__in=['purchased', 'closed'])
    # New submissions take priority; previously held automatic drafts rotate
    # by update time so neither new work nor other held drafts is starved.
    ids = list(eligible.filter(telegram_publication__isnull=True)
               .order_by('pk').values_list('pk', flat=True)[:limit])
    if len(ids) < limit:
        ids += list(eligible.filter(
            telegram_publication__status='draft',
            telegram_publication__reviewed=False,
            telegram_publication__text='',
            telegram_publication__created_by__isnull=True,
        ).order_by('telegram_publication__updated_at', 'pk')
          .values_list('pk', flat=True)[:limit - len(ids)])
    count = 0
    for pk in ids:
        with transaction.atomic():
            req = Request.objects.select_for_update().get(pk=pk)
            if not req.public_consent_at or req.purchase_result in ('purchased', 'closed'):
                continue
            publication = TelegramRequestPublication.objects.filter(request=req).first()
            if publication and not (publication.status == 'draft' and not publication.reviewed
                                    and not publication.text and publication.created_by_id is None):
                continue
            summary = safe_automatic_description(req)
            fields = [req.brand, req.model, req.city]
            unsafe = (not summary or any(
                PII.search(x or '') or SENSITIVE.search(x or '') for x in fields))
            if unsafe:
                if publication:
                    publication.save(update_fields=['updated_at'])
                else:
                    TelegramRequestPublication.objects.create(request=req, text='', reviewed=False)
                continue
            req.public_summary = summary
            req.seller_feed_enabled = True
            req.save(update_fields=['public_summary', 'seller_feed_enabled'])
            text = safe_summary(req)
            if not text:
                continue
            if publication:
                publication.text = text
                publication.reviewed = True
                publication.save(update_fields=['text', 'reviewed', 'updated_at'])
            else:
                TelegramRequestPublication.objects.create(request=req, text=text, reviewed=True)
    # Separate preparation from delivery: a worker interruption after creating
    # the durable draft cannot strand it. Attempts with unknown outcomes are
    # never retried automatically, including a crash during sendMessage.
    drafts = TelegramRequestPublication.objects.filter(
        status='draft', reviewed=True, message_id__isnull=True,
        request__public_consent_at__isnull=False,
        request__connection_consents__source='request_submit',
        request__connection_consents__text_version=VERSION,
        request__connection_consents__purpose='public',
        request__connection_consents__granted=True,
    ).exclude(request__purchase_result__in=['purchased', 'closed']).order_by('pk')[:limit]
    for publication in drafts:
        try:
            publish(publication.pk, None)
            count += 1
        except ValueError:
            logger.warning('Telegram publication requires review: request #%s', publication.request_id)
    return count
