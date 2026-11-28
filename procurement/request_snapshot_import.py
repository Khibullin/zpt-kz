"""Bind a new Apify result to its immutable supplier selection and price snapshot."""
from collections import defaultdict
from copy import copy
from pathlib import PurePath

from django.core.exceptions import ValidationError, ObjectDoesNotExist
from .models import Activity, Offer
from .omega_selection_requests import REQUEST_ACTION, request_rows, _cost


def selected_request(request_id):
    if not request_id:
        return None
    try:
        saved = Activity.objects.get(pk=int(request_id), action=REQUEST_ACTION)
        # Validates the original supplier price hash, not today's catalogue.
        validate_snapshot_rows(request_rows(saved))
        return saved
    except (ObjectDoesNotExist, ValueError, TypeError, KeyError) as exc:
        raise ValidationError('Не найден сохранённый запрос с исходным прайсом.') from exc


def upload_request(request, supplier):
    key = request.POST.get('request_id') or request.session.get('apify_prepared_request_id')
    saved = selected_request(key)
    if not saved and Activity.objects.filter(action=REQUEST_ACTION, details__supplier_id=supplier.pk,
            details__processing_policy='request_snapshot_v1').exists():
        raise ValidationError('Выберите сохранённый запрос в блоке 2 перед загрузкой новых результатов.')
    if saved and saved.details.get('supplier_id') != supplier.pk:
        raise ValidationError('Выберите запрос выбранного поставщика в блоке подготовки Apify.')
    return saved


def intake_context(request_id):
    saved = selected_request(request_id)
    if not saved:
        return {}
    return {'selection_request_id': saved.pk, 'selection_title': saved.details['title'],
            'supplier_checked_on': saved.details.get('checked_on', ''),
            'supplier_id': saved.details['supplier_id'],
            'price_batch_id': saved.details['price_batch_id'],
            'price_sha256': saved.details['price_sha256']}


def audit_source(filename, request_id):
    basename = PurePath(filename).name
    if not request_id:
        return basename[:255]
    # All received parts of this new request share one minimum. Original
    # filenames remain in intake sessions and selected seller evidence.
    return f'request_{request_id}.json'


def scoped_offer_index(saved, brands):
    from .apify_price_recheck import _brand, normalize_article
    rows = [row for row in request_rows(saved) if _brand(row['brand']) in brands]
    validate_snapshot_rows(rows)
    wanted = {(_brand(row['brand']), normalize_article(row['article'])) for row in rows}
    existing = defaultdict(list)
    # Read only identity columns once. This also reuses differently formatted
    # existing SKUs instead of creating case/dash/space duplicates.
    for pk, brand, article in Offer.objects.filter(supplier_id=saved.details['supplier_id']).order_by().values_list(
            'pk', 'brand', 'article').iterator(chunk_size=1500):
        key = (_brand(brand), normalize_article(article))
        if key in wanted:
            existing[key].append(Offer(pk=pk, supplier_id=saved.details['supplier_id'], brand=brand, article=article))
    index = defaultdict(lambda: defaultdict(list))
    for row in rows:
        brand, article = str(row['brand']).strip(), str(row['article']).strip()
        key = (_brand(brand), normalize_article(article))
        candidates = existing.get(key, [])
        original = next((offer for offer in candidates if offer.brand == brand and offer.article == article),
                        candidates[0] if candidates else None)
        offer = copy(original) if original else Offer(supplier_id=saved.details['supplier_id'], brand=brand, article=article)
        # Only this in-memory calculation changes; existing Offer stays untouched.
        offer.brand, offer.article = brand, article
        offer.cost = _cost(row)
        offer.stock = str(row.get('stock') or '')
        offer.name = str(row.get('name') or '')[:500]
        offer.category = str(row.get('category') or '')[:200]
        offer.applicability = str(row.get('applicability') or '')
        offer.supplier_checked_on = saved.details.get('checked_on')
        index[key[0]][key[1]].append(offer)
    return index


def supplier_snapshot_date(audit, fallback):
    from datetime import date
    value = (audit.price_evidence or {}).get('supplier_checked_on') if audit else None
    try:
        return date.fromisoformat(value) if value else fallback
    except (ValueError, TypeError):
        return fallback


def validate_snapshot_rows(rows):
    """Equivalent article formatting cannot silently choose a cheaper cost row."""
    from .apify_price_recheck import _brand, normalize_article
    seen = {}
    for row in rows:
        key = (_brand(row['brand']), normalize_article(row['article']))
        value = (_cost(row), str(row.get('_pack_size') or 1))
        if key in seen and seen[key] != value:
            raise ValidationError('В прайсе разные цены или упаковки для одного бренда/артикула: '
                                  + str(row['brand']) + ' ' + str(row['article']) + '. Уточните прайс до запуска Apify.')
        seen[key] = value
