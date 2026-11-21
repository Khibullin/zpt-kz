"""Bounded, evidence-only enrichment for profitable buying comparisons."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

from .kaspi_enrichment import fetch_page, exact_products
from .models import BuyingComparisonMaterial, KaspiPriceAudit, PermanentBuyingItem

PRODUCT_ID_RE = re.compile(r'-(\d{6,15})/?$')


def _identifier(url):
    m = PRODUCT_ID_RE.search(urlsplit(url or '').path)
    return m.group(1) if m else ''


def _string(value):
    if value is None or value == '':
        return ''
    if isinstance(value, (list, tuple)):
        return '; '.join(str(x) for x in value if x)
    return str(value).strip()


def _accepted_image(url, page_url):
    value = urljoin(page_url, str(url or ''))
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname:
        return ''
    host = parsed.hostname.lower()
    if host == 'resources.cdn-kaspi.kz' or host in ('kaspi.kz', 'omega-auto-parts.kz') or host.endswith('.omega-auto-parts.kz'):
        return value[:2048]
    return ''


def _extract(url, brand, article):
    # Existing fetch_page has redirect, public-IP, time and size protections.
    page = fetch_page(url)
    for obj in exact_products(page, brand, article):
        specs = {}
        properties = obj.get('additionalProperty') or []
        if not isinstance(properties, list):
            properties = [properties]
        for prop in properties:
            if isinstance(prop, dict) and prop.get('name') and prop.get('value') not in (None, ''):
                specs[str(prop['name'])[:120]] = str(prop['value'])[:500]
        norm = {k.casefold(): v for k, v in specs.items()}
        def pick(*names):
            return next((norm[name.casefold()] for name in names if norm.get(name.casefold())), '')
        maker = obj.get('brand') or ''
        if isinstance(maker, dict):
            maker = maker.get('name') or ''
        images = obj.get('image') or []
        if not isinstance(images, list):
            images = [images]
        found = []
        for item in images:
            if isinstance(item, dict):
                item = item.get('url') or item.get('contentUrl')
            photo = _accepted_image(item, url)
            if photo and photo not in found:
                found.append(photo)
        return {
            'name': _string(obj.get('name'))[:500],
            'brand': _string(maker)[:120],
            'article': _string(obj.get('mpn') or obj.get('sku'))[:120],
            'category': _string(obj.get('category'))[:300],
            'description': _string(obj.get('description'))[:5000],
            'specs': specs,
            'fitment': pick('Применимость', 'Совместимость', 'Применение'),
            'package': pick('Комплектность', 'Комплектация'),
            'oem': pick('OEM', 'Оригинальные номера', 'Оригинальный номер', 'OE номер'),
            'analogs': pick('Аналоги', 'Заменители'),
            'images': found[:8], 'url': url,
        }
    raise ValueError('На странице нет подтверждённой структурированной карточки с точным брендом и артикулом')


def enrich_one(record, *, force=False):
    offer = record.offer
    audit = KaspiPriceAudit.objects.filter(offer=offer).order_by('-observed_at', '-pk').first()
    if audit:
        source, product_id, kaspi_url = audit.source_filename, audit.product_id, audit.card_url
    else:
        source, product_id, kaspi_url = '', record.product_id, record.kaspi_url
    if not product_id or not kaspi_url or _identifier(kaspi_url) != product_id:
        return 'no_kaspi_identity'
    material, _ = BuyingComparisonMaterial.objects.get_or_create(
        offer=offer, source_filename=source, kaspi_product_id=product_id,
        defaults={'supplier_url':offer.supplier_url or '', 'kaspi_url':kaspi_url},
    )
    changes = []
    targets = [('supplier', offer.supplier_url), ('kaspi', kaspi_url)]
    for kind, url in targets:
        status_key, data_key, error_key = kind+'_status', kind+'_data', kind+'_error'
        if not force and getattr(material, status_key) == 'complete':
            continue
        # No HTTP requests without verified source URL and exact card identity.
        expected_host = ('omega-auto-parts.kz' if kind == 'supplier' else 'kaspi.kz')
        host = (urlsplit(url or '').hostname or '').lower()
        if not url or (host != expected_host and not host.endswith('.'+expected_host)):
            setattr(material, status_key, 'missing_source')
            setattr(material, error_key, 'Отсутствует точная ссылка источника')
            changes.extend([status_key, error_key])
            continue
        try:
            result = _extract(url, offer.brand, offer.article)
        except Exception as exc:
            setattr(material, status_key, 'unavailable')
            setattr(material, error_key, str(exc)[:250])
            changes.extend([status_key, error_key])
            continue
        setattr(material, data_key, result)
        setattr(material, status_key, 'complete')
        setattr(material, error_key, '')
        changes.extend([data_key, status_key, error_key])
    material.supplier_url = offer.supplier_url or ''
    material.kaspi_url = kaspi_url
    material.save(update_fields=list(set(changes + ['supplier_url','kaspi_url','checked_at'])))
    return 'complete' if material.supplier_status == material.kaspi_status == 'complete' else 'partial'


def batch(limit=20, *, force=False):
    """Incremental, restartable, no change to pricing or other source tables."""
    from django.db.models import OuterRef, Subquery
    audits = KaspiPriceAudit.objects.filter(offer_id=OuterRef('offer_id')).order_by('-observed_at', '-pk')
    candidates = PermanentBuyingItem.objects.select_related('offer').annotate(
        chosen_source=Subquery(audits.values('source_filename')[:1]),
        chosen_product=Subquery(audits.values('product_id')[:1]),
    ).order_by('pk')
    totals = {'complete':0, 'partial':0, 'no_kaspi_identity':0, 'examined':0}
    for record in candidates.iterator(chunk_size=100):
        if totals['examined'] >= limit:
            break
        source = record.chosen_source or ''
        pid = record.chosen_product or record.product_id
        if not force and BuyingComparisonMaterial.objects.filter(
                offer_id=record.offer_id, source_filename=source, kaspi_product_id=pid,
                supplier_status__in=('complete','unavailable','missing_source'),
                kaspi_status__in=('complete','unavailable','missing_source')).exists():
            continue
        result = enrich_one(record, force=force)
        totals[result] += 1
        totals['examined'] += 1
    return totals
