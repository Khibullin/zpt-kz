"""Truthful Schema.org organization JSON-LD for public directory detail pages."""
import json

from django import template
from django.utils.safestring import mark_safe

from core.phone_utils import normalize_phone_for_whatsapp
from core.seo import canonical_url_for_path

register = template.Library()


@register.simple_tag
def organization_json_ld(seller, kind='parts'):
    if kind in ('sto', 'detailing'):
        name = str(getattr(seller, 'name', '') or '').strip()
        address = str(getattr(seller, 'address', '') or '').strip()
        city = str(getattr(seller, 'city', '') or '').strip()
        description = str(getattr(seller, 'description', '') or '').strip()
        path = f'/catalog/services/{seller.pk}/'
        business_type = 'AutoRepair' if kind == 'sto' else 'AutomotiveBusiness'
    else:
        name = str(getattr(seller, 'name', '') or '').strip()
        address = str(getattr(seller, 'address', '') or '').strip()
        city = str(getattr(seller, 'city', '') or '').strip()
        description = str(getattr(seller, 'directory_description', '') or '').strip()
        path = f'/parts-seller/{seller.pk}/'
        business_type = 'AutoPartsStore' if kind == 'parts' else 'LocalBusiness'

    if not name:
        return ''
    canonical_url = canonical_url_for_path(path)
    data = {
        '@context': 'https://schema.org',
        '@type': business_type,
        '@id': f'{canonical_url}#organization',
        'name': name,
        'url': canonical_url,
    }
    if city or address:
        postal = {'@type': 'PostalAddress', 'addressCountry': 'KZ'}
        if city:
            postal['addressLocality'] = city
        if address:
            postal['streetAddress'] = address
        data['address'] = postal
    phone = normalize_phone_for_whatsapp(getattr(seller, 'whatsapp', '') or '')
    if phone:
        data['telephone'] = f'+{phone}'
    if description:
        data['description'] = description[:500]

    # Serialize as data, never interpolate untrusted names into executable JS.
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    payload = (
        payload.replace('&', r'\u0026')
        .replace('<', r'\u003C')
        .replace('>', r'\u003E')
        .replace('\u2028', r'\u2028')
        .replace('\u2029', r'\u2029')
    )
    return mark_safe(payload)
