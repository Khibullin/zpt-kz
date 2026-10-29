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
            seller.clean()
            seller.save(update_fields=list(values))
            sellers.append(seller)
        return sellers
