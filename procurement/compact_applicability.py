"""Read saved applicability on the current buying page, without altering source records."""
import re
from django.db.models import Q
from django.db.models.fields.json import KeyTextTransform
from .models import BuyingComparisonMaterial, VehicleFitment


def _text(value):
    if isinstance(value, (list, tuple)):
        return '; '.join(filter(None, map(_text, value)))
    if isinstance(value, dict):
        return '; '.join(f'{k}: {_text(v)}' for k, v in value.items() if _text(v))
    value = str(value or '').strip()
    return '' if value.casefold() in {'нет данных', 'неизвестно', 'не указано', '—', '-', 'null'} else value


def _key(value):
    return re.sub(r'[^\w]+', '', value.casefold())


def _unique(values):
    result = {}
    for value in values:
        value = _text(value)
        if value:
            result.setdefault(_key(value), value)
    return list(result.values())


def compact_summary(models, supplier, kaspi):
    models = _unique(models)
    supplier, kaspi = _text(supplier), _text(kaspi)
    texts = _unique([supplier, kaspi])
    full = '; '.join(_unique(models + texts))
    if not full:
        return 'Применимость не установлена', '', False
    # Do not interpret unstructured source text as a verified vehicle pair.
    # Different source wording may indicate a conflict, not confirmed incompatibility.
    conflict = bool(supplier and kaspi and _key(supplier) != _key(kaspi)
                    and _key(supplier) not in _key(kaspi)
                    and _key(kaspi) not in _key(supplier))
    if models:
        short = models[0] + (f' · + ещё {len(models)-1} моделей' if len(models)>1 else '')
        # Keep other source information visible when it supplies additional data.
        extras = [t for t in texts if not all(_key(t) in _key(m) for m in models)
                  and not all(_key(part) in {_key(m) for m in models}
                              for part in t.split(';') if part.strip())]
        if extras:
            short += ' · ' + ' / '.join(extras)
    else:
        short = ' / '.join(texts)
    return 'Автомобиль: ' + short, full, conflict


def populate_compact_applicability(rows):
    ids = [r['offer'].pk for r in rows]
    if not ids:
        return
    pairs = {}
    for pk, make, model in VehicleFitment.objects.filter(
            offer_id__in=ids, verified=True).order_by('make', 'model').values_list('offer_id', 'make', 'model').distinct():
        if make and model:
            pairs.setdefault(pk, []).append(f'{make} {model}')
    # Match the same source/card as the detailed comparison; do not transfer
    # Kaspi information between different product IDs or exports.
    keys = {}
    condition = Q()
    for row in rows:
        candidate = row.get('candidate') or {}
        product = str(candidate.get('id') or '')
        source = row.get('audit_file') or ''
        if product:
            key = (row['offer'].pk, source, product)
            keys[row['offer'].pk] = key
            condition |= Q(offer_id=key[0], source_filename=source, kaspi_product_id=product)
    materials = {}
    if keys:
        # Extract only small text fields, not the stored screenshot images.
        for item in BuyingComparisonMaterial.objects.filter(condition).annotate(
                sf=KeyTextTransform('fitment', 'supplier_data'),
                sa=KeyTextTransform('applicability', 'supplier_data'),
                kf=KeyTextTransform('fitment', 'kaspi_data'),
                ka=KeyTextTransform('applicability', 'kaspi_data'),
        ).values_list('offer_id','source_filename','kaspi_product_id','sf','sa','kf','ka'):
            materials[item[:3]] = item[3:]
    for row in rows:
        offer = row['offer']
        models = pairs.get(offer.pk, [])
        key = keys.get(offer.pk)
        sf, sa, kf, ka = materials.get(key, (None, None, None, None))
        supplier = _text(sf) or _text(sa) or '; '.join(models) or _text(offer.applicability)
        characteristics = offer.characteristics if isinstance(offer.characteristics, dict) else {}
        saved = characteristics.get('kaspi_card_details') or characteristics.get('kaspi_details') or {}
        kaspi = _text(kf) or _text(ka)
        if not kaspi and key and isinstance(saved, dict) and str(saved.get('product_id', '')) == key[2]:
            kaspi = _text(saved.get('fitment')) or _text(saved.get('applicability'))
        row['applicability_short'], row['applicability_full'], row['applicability_conflict'] = compact_summary(models, supplier, kaspi)
