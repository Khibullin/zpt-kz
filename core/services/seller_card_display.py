"""Public presentation of a parts-seller card.

Only stored values become links. Route buttons use coordinates.
Source buttons use saved card URLs and never a name search.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from core.phone_utils import normalize_phone_for_whatsapp, seller_whatsapp_url, seller_whatsapp_phone, is_excluded_city_whatsapp
from core.services.seller_card_links import validate_card_url, whatsapp_digits

DAY_LABELS = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
SOCIAL_LABELS = {
    'whatsapp': 'WhatsApp',
    'instagram': 'Instagram',
    'telegram': 'Telegram',
    'tiktok': 'TikTok',
    'vk': 'VK',
    'youtube': 'YouTube',
}
SOURCE_LABELS = {
    'two_gis': 'Посмотреть в 2ГИС',
    'yandex': 'Посмотреть в Яндексе',
    'google': 'Посмотреть в Google Картах',
}
SOURCE_ORDER = ('two_gis', 'yandex', 'google')


def format_phone(value: object) -> str:
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 11 and digits.startswith('7'):
        return f'+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}'
    normalized = normalize_phone_for_whatsapp(value)
    if normalized:
        return f'+{normalized}'
    return ''


def route_links(latitude, longitude) -> list[dict]:
    if latitude is None or longitude is None:
        return []
    lat = format(Decimal(latitude), 'f')
    lon = format(Decimal(longitude), 'f')
    return [
        {
            'label': '2ГИС',
            'url': f'https://2gis.kz/directions/points/|{lon},{lat}',
        },
        {
            'label': 'Google Карты',
            'url': f'https://www.google.com/maps/dir/?api=1&destination={lat},{lon}',
        },
        {
            'label': 'Яндекс Карты',
            'url': f'https://yandex.kz/maps/?rtext=~{lat},{lon}&rtt=auto',
        },
    ]


def _schedule_rows(seller) -> list[dict]:
    grouped = defaultdict(list)
    closed = set()
    for interval in seller.work_hours.all():
        if interval.is_closed:
            closed.add(interval.weekday)
            continue
        if interval.opens and interval.closes:
            grouped[interval.weekday].append(
                f'{interval.opens.strftime("%H:%M")}–{interval.closes.strftime("%H:%M")}'
            )
    rows = []
    for weekday in range(7):
        if weekday in closed and weekday not in grouped:
            rows.append({'day': DAY_LABELS[weekday], 'hours': 'выходной'})
        elif weekday in grouped:
            rows.append({'day': DAY_LABELS[weekday], 'hours': ', '.join(grouped[weekday])})
    return rows


def _phone_entry(raw: object) -> dict | None:
    digits = normalize_phone_for_whatsapp(raw)
    if not digits:
        return None
    return {
        'display': format_phone(digits),
        'tel': f'tel:+{digits}',
        'digits': digits,
    }


def build_seller_card(seller) -> dict:
    primary = _phone_entry(seller.whatsapp) or _phone_entry(seller.phone2)
    if primary:
        primary['label'] = seller.directory_contact_label if _phone_entry(seller.whatsapp) else seller.directory_phone2_label
    seen = {primary['digits']} if primary else set()
    extra_phones = []
    for raw, label in [(seller.phone2, seller.directory_phone2_label), *[(item.phone, item.label) for item in seller.extra_phones.all()]]:
        entry = _phone_entry(raw)
        if entry:
            entry['label'] = label
        if entry and entry['digits'] not in seen:
            seen.add(entry['digits'])
            extra_phones.append(entry)
    socials = []
    primary_wa = seller_whatsapp_url(seller)
    primary_digits = seller_whatsapp_phone(seller)
    for link in seller.social_links.all():
        if link.kind == 'whatsapp':
            digits = whatsapp_digits(link.url)
            if is_excluded_city_whatsapp(digits) or (digits and digits == primary_digits):
                continue
        socials.append({
            'kind': link.kind,
            'label': SOCIAL_LABELS.get(link.kind, link.kind),
            'url': link.url,
        })
    sources = []
    cards = {card.source: card for card in seller.external_cards.all()}
    for source in SOURCE_ORDER:
        card = cards.get(source)
        if card is None:
            continue
        url, error = validate_card_url(source, card.card_url)
        if error or not url:
            continue
        sources.append({'label': SOURCE_LABELS[source], 'url': url})
    website = (seller.website or '').strip()
    if website and not website.startswith(('http://', 'https://')):
        website = ''
    return {
        'address': (seller.address or '').strip(),
        'routes': route_links(seller.latitude, seller.longitude),
        'schedule': _schedule_rows(seller),
        'primary_phone': primary,
        'extra_phones': extra_phones,
        'whatsapp_url': primary_wa,
        'socials': socials,
        'website': website,
        'sources': sources,
        'city': (seller.city or '').strip(),
        'market_location': (seller.market_location or '').strip(),
        'notes': (seller.notes or '').strip(),
    }
