"""External verification facts; independent of consent and sales guarantees."""
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def safe_url(value):
    if not isinstance(value, str):
        return ''
    try:
        URLValidator(schemes=['http', 'https'])(value)
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            return ''
    except (ValidationError, ValueError):
        return ''
    return value


def validate_sources(value):
    if not isinstance(value, list) or len(value) > 30:
        raise ValidationError('Укажите список не более 30 источников.')
    for source in value:
        if not isinstance(source, dict) or not isinstance(source.get('label'), str) or not source['label'].strip():
            raise ValidationError('У каждого источника нужны label и url.')
        if not safe_url(source.get('url')):
            raise ValidationError('Источник должен иметь корректную ссылку http/https без логина и пароля.')
        for field in ('confirmed', 'public'):
            if field in source and type(source[field]) is not bool:
                raise ValidationError('confirmed и public должны быть true или false.')
        if source.get('public') and not source.get('confirmed'):
            raise ValidationError('Нельзя показывать покупателю неподтверждённый источник.')


def presentation(seller, *, internal=False):
    """Whitelist public fields rather than passing the internal audit to templates."""
    status = seller.audit_status
    if not internal and (status != 'linked' or not seller.audit_checked_at):
        return None
    sources = []
    raw = seller.audit_sources if isinstance(seller.audit_sources, list) else []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = safe_url(item.get('url'))
        if not url or (not internal and not (item.get('confirmed') is True and item.get('public') is True)):
            continue
        if not internal and (item.get('kind') == 'kaspi' or urlsplit(url).hostname in ('kaspi.kz', 'www.kaspi.kz')) and seller.audit_kaspi != 'confirmed':
            continue
        sources.append({'label': str(item.get('label', 'Источник')), 'url': url,
                        'confirmed': item.get('confirmed') is True})
    result = {'status': seller.get_audit_status_display(), 'checked_at': seller.audit_checked_at,
              'summary': seller.audit_public_summary, 'city': seller.audit_city,
              'address': seller.audit_address, 'delivery': seller.audit_delivery,
              'sources': sources, 'kaspi': seller.get_audit_kaspi_display() if internal or seller.audit_kaspi == 'confirmed' else '',
              'activity': seller.get_audit_activity_display(), 'signal_date': seller.audit_signal_date}
    if internal:
        result.update(note=seller.audit_note, conflict=status == 'conflict',
                      checked=status != 'unchecked')
    return result


def import_audits(rows):
    """Import only audit fields into exact, unchanged identities; all or nothing."""
    from django.db import transaction
    from core.models import Seller
    fields = {field.name for field in Seller._meta.fields if field.name.startswith('audit_')}
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValidationError('Укажите от 1 до 100 карточек проверки.')
    ids = set()
    with transaction.atomic():
        sellers = []
        for row in rows:
            if not isinstance(row, dict) or type(row.get('pk')) is not int or row['pk'] in ids:
                raise ValidationError('Некорректный или повторяющийся ID продавца.')
            ids.add(row['pk'])
            values = row.get('values')
            if not isinstance(values, dict) or not values or set(values) - fields:
                raise ValidationError('Можно изменять только поля audit_ проверки продавца.')
            seller = Seller.objects.select_for_update().filter(pk=row['pk'], is_test_seller=False,
                name=row.get('name'), whatsapp=row.get('phone'), city=row.get('city'),
                audit_status=row.get('expected_audit_status', 'unchecked')).first()
            if seller is None:
                raise ValidationError(f"Карточка №{row['pk']} изменилась или не соответствует исходным данным. Импорт отменён.")
            for key, value in values.items():
                setattr(seller, key, Seller._meta.get_field(key).clean(value, seller))
            if 'audit_evidence' in values:
                bind_evidence(seller)
            seller.clean()
            seller.save(update_fields=list(values))
            sellers.append(seller)
        return sellers


CRITERIA = (
    ('contacts', 'Контакты'), ('location', 'Город и адрес'),
    ('specialization', 'Специализация'), ('independent', 'Независимые источники'),
    ('freshness', 'Свежесть торговли'),
)


def validate_evidence(value):
    if not isinstance(value, dict) or set(value) - {key for key, _ in CRITERIA}:
        raise ValidationError('Допустимы только contacts, location, specialization, independent, freshness.')
    for item in value.values():
        if not isinstance(item, dict) or type(item.get('confirmed', False)) is not bool:
            raise ValidationError('Критерий должен содержать confirmed: true/false, urls и note.')
        urls = item.get('urls', [])
        if not isinstance(urls, list) or len(urls) > 10 or any(not safe_url(url) for url in urls):
            raise ValidationError('Укажите до 10 корректных ссылок в urls.')
        if not isinstance(item.get('note', ''), str):
            raise ValidationError('Основание note должно быть текстом.')
        if item.get('confirmed') and (not urls or not item.get('note', '').strip()):
            raise ValidationError('Подтверждённому критерию нужны ссылки и текстовое основание.')


def source_group(url):
    host = (urlsplit(url).hostname or '').removeprefix('www.')
    if host in ('2gis.kz', '2gis.ru'):
        return '2gis'
    return '.'.join(host.split('.')[-2:])


def rating(seller, *, check_identity=True, check_freshness=True):
    """Five explicit evidence points, evaluated afresh; no points for completeness alone."""
    from datetime import timedelta
    from django.utils import timezone
    checked = seller.audit_status != 'unchecked' and bool(seller.audit_checked_at)
    valid_sources = set()
    raw_sources = seller.audit_sources if isinstance(seller.audit_sources, list) else []
    for source in raw_sources:
        if not isinstance(source, dict) or source.get('confirmed') is not True:
            continue
        url = safe_url(source.get('url'))
        if not url:
            continue
        if (source.get('kind') == 'kaspi' or source_group(url) == 'kaspi.kz') and seller.audit_kaspi != 'confirmed':
            continue
        valid_sources.add(url)
    raw = seller.audit_evidence if isinstance(getattr(seller, 'audit_evidence', None), dict) else {}
    criteria = []
    for key, label in CRITERIA:
        item = raw.get(key, {})
        item = item if isinstance(item, dict) else {}
        urls = item.get('urls', [])
        urls = urls if isinstance(urls, list) else []
        confirmed = bool(checked and item.get('confirmed') is True and isinstance(item.get('note'), str) and item['note'].strip()
                         and urls and all(isinstance(url, str) and url in valid_sources for url in urls))
        if check_identity:
            confirmed = confirmed and item.get('identity') == evidence_identity(seller, key)
        if key == 'independent':
            confirmed = confirmed and len({source_group(url) for url in urls if isinstance(url, str) and safe_url(url)}) >= 2
        if key == 'freshness' and check_freshness:
            today = timezone.localdate()
            confirmed = bool(confirmed and seller.audit_activity == 'recent' and seller.audit_signal_date
                             and today - timedelta(days=180) <= seller.audit_signal_date <= today)
        if key == 'location' and seller.audit_status == 'conflict':
            confirmed = False
        criteria.append({'key': key, 'label': label, 'confirmed': confirmed,
                         'state': 'Подтверждено' if confirmed else ('Данные изменились' if check_identity and item.get('identity') and item.get('identity') != evidence_identity(seller, key) else 'Нет подтверждения'),
                         'note': str(item.get('note', '')),
                         'sources': [{'url': url, 'label': urlsplit(url).hostname} for url in urls if isinstance(url, str) and safe_url(url)]})
    score = sum(item['confirmed'] for item in criteria) if checked else None
    return {'score': score, 'checked': checked, 'criteria': criteria,
            'segments': [checked and number < score for number in range(5)],
            'conflict': seller.audit_status == 'conflict', 'checked_at': seller.audit_checked_at}


def validate_evidence_for_seller(seller):
    validate_evidence(seller.audit_evidence)
    result = rating(seller, check_identity=False, check_freshness=False)
    for criterion in result['criteria']:
        if seller.audit_evidence.get(criterion['key'], {}).get('confirmed') and not criterion['confirmed']:
            raise ValidationError({'audit_evidence': 'Не подтверждён критерий «' + criterion['label'] +
                '»: проверьте источники, дату, статус Kaspi и расхождения. Свежесть — не более 180 дней.'})


def evidence_identity(seller, key):
    fields = ('name', 'city', 'address') if key == 'location' else ('name', 'whatsapp')
    return {field: ' '.join(str(getattr(seller, field, '') or '').split()).casefold() for field in fields}


def bind_evidence(seller):
    if isinstance(seller.audit_evidence, dict):
        for key, item in seller.audit_evidence.items():
            if isinstance(item, dict) and item.get('confirmed') is True:
                item['identity'] = evidence_identity(seller, key)
