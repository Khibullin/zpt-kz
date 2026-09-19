"""Brand/model suggestions from the core vehicle catalog. No paid APIs."""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.models import Brand, CarModel, TRANSPORT_CHOICES

SUGGEST_LIMIT = 12
MIN_QUERY_LEN = 1
TYPO_MIN_LEN = 4
TYPO_MAX_DISTANCE = 2

_BRAND_KEY_RE = re.compile(r'[^a-zа-я0-9]+', re.IGNORECASE)

_RU_TO_LAT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

BRAND_ALIASES = {
    'тойота': 'Toyota',
    'toyota': 'Toyota',
    'бмв': 'BMW',
    'bmw': 'BMW',
    'мерседес': 'Mercedes-Benz',
    'mercedes': 'Mercedes-Benz',
    'mercedes-benz': 'Mercedes-Benz',
    'фольксваген': 'Volkswagen',
    'volkswagen': 'Volkswagen',
    'vw': 'Volkswagen',
    'хендай': 'Hyundai',
    'hyundai': 'Hyundai',
    'киа': 'Kia',
    'kia': 'Kia',
    'ниссан': 'Nissan',
    'nissan': 'Nissan',
    'хонда': 'Honda',
    'honda': 'Honda',
    'мазда': 'Mazda',
    'mazda': 'Mazda',
    'лексус': 'Lexus',
    'lexus': 'Lexus',
    'ауди': 'Audi',
    'audi': 'Audi',
    'шкода': 'Skoda',
    'skoda': 'Skoda',
    'рено': 'Renault',
    'renault': 'Renault',
    'форд': 'Ford',
    'ford': 'Ford',
    'шевроле': 'Chevrolet',
    'chevrolet': 'Chevrolet',
    'чери': 'Chery',
    'chery': 'Chery',
    'хавал': 'Haval',
    'haval': 'Haval',
    'джили': 'Geely',
    'geely': 'Geely',
    'чанган': 'Changan',
    'changan': 'Changan',
    'бид': 'BYD',
    'byd': 'BYD',
    'танк': 'Tank',
    'tank': 'Tank',
    'зикр': 'Zeekr',
    'zeekr': 'Zeekr',
    'лисян': 'Li Auto',
    'lixiang': 'Li Auto',
    'li auto': 'Li Auto',
    'li': 'Li Auto',
    'грейт вол': 'Great Wall',
    'great wall': 'Great Wall',
    'gwm': 'Great Wall',
    'gw': 'Great Wall',
    'jetour': 'Jetour',
    'джетур': 'Jetour',
    'exeed': 'Exeed',
    'omoda': 'Omoda',
    'jaecoo': 'Jaecoo',
}

MODEL_ALIASES = {
    'джолион': 'Jolion',
    'джольон': 'Jolion',
    'джол': 'Jolion',
    'jolion': 'Jolion',
    'camry': 'Camry',
    'камри': 'Camry',
    'королла': 'Corolla',
    'corolla': 'Corolla',
    'рио': 'Rio',
    'rio': 'Rio',
    'солярис': 'Solaris',
    'solaris': 'Solaris',
    'тигго': 'Tiggo',
    'tiggo': 'Tiggo',
}

ALIAS_CANONICAL = {**BRAND_ALIASES, **MODEL_ALIASES}
ALIAS_PREFIX_MIN_LEN = 3


@dataclass(frozen=True)
class VehicleMatch:
    brand_id: int | None
    brand_name: str
    model_id: int | None
    model_name: str
    country: str
    transport_type: str
    ambiguous: bool


def _fold(value: str) -> str:
    return ' '.join(str(value or '').split()).casefold().replace('ё', 'е')


def _brand_key(value: str) -> str:
    return _BRAND_KEY_RE.sub('', _fold(value))


def _transliterate_ru(value: str) -> str:
    return ''.join(_RU_TO_LAT.get(ch, ch) for ch in _fold(value))


def _alias_canonical(value: str) -> str:
    key = _fold(value)
    return ALIAS_CANONICAL.get(key, '')


def _alias_prefix_names(query: str) -> list[str]:
    q = _fold(query)
    if len(q) < ALIAS_PREFIX_MIN_LEN:
        return []
    names = []
    seen = set()
    for alias, canonical in ALIAS_CANONICAL.items():
        if alias.startswith(q) or q.startswith(alias):
            folded = _fold(canonical)
            if folded in seen:
                continue
            seen.add(folded)
            names.append(canonical)
    return names


def _levenshtein(left: str, right: str, max_dist: int) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > max_dist:
        return max_dist + 1
    previous = list(range(len(right) + 1))
    for i, left_ch in enumerate(left, start=1):
        current = [i]
        min_row = i
        for j, right_ch in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (left_ch != right_ch)
            value = min(insert_cost, delete_cost, replace_cost)
            current.append(value)
            if value < min_row:
                min_row = value
        if min_row > max_dist:
            return max_dist + 1
        previous = current
    return previous[-1]


def _score_name(query: str, name: str) -> int | None:
    q = _fold(query)
    n = _fold(name)
    if not q or not n:
        return None
    if q == n:
        return 0
    alias = _fold(_alias_canonical(query))
    if alias and alias == n:
        return 1
    for canonical in _alias_prefix_names(query):
        folded = _fold(canonical)
        if folded == n or n.startswith(folded) or folded.startswith(n):
            return 3
        n_key = _brand_key(name)
        c_key = _brand_key(canonical)
        if c_key and n_key and (n_key == c_key or n_key.startswith(c_key) or c_key in n_key):
            return 4
    q_lat = _transliterate_ru(query)
    n_lat = _transliterate_ru(name)
    if q_lat and q_lat == n_lat:
        return 2
    if n.startswith(q) or n_lat.startswith(q_lat):
        return 10
    if q in n or q_lat in n_lat:
        return 20
    q_key = _brand_key(query)
    n_key = _brand_key(name)
    if q_key and n_key and (n_key.startswith(q_key) or q_key in n_key):
        return 25
    if len(q) >= TYPO_MIN_LEN:
        dist = _levenshtein(q, n, TYPO_MAX_DISTANCE)
        if dist <= TYPO_MAX_DISTANCE:
            return 40 + dist
        dist_lat = _levenshtein(q_lat, n_lat, TYPO_MAX_DISTANCE)
        if dist_lat <= TYPO_MAX_DISTANCE:
            return 45 + dist_lat
    return None


def _transport_label(transport_type: str) -> str:
    return dict(TRANSPORT_CHOICES).get(transport_type, '')


def suggest_brands(query: str, *, limit: int = SUGGEST_LIMIT) -> list[dict]:
    text = ' '.join(str(query or '').split())
    if len(text) < MIN_QUERY_LEN:
        return []

    scored: list[tuple[int, str, Brand]] = []
    for brand in Brand.objects.select_related('country').order_by('name', 'id'):
        score = _score_name(text, brand.name)
        alias = _alias_canonical(text)
        if score is None and alias:
            score = _score_name(alias, brand.name)
        if score is None:
            continue
        scored.append((score, brand.name.casefold(), brand))

    scored.sort(key=lambda item: (item[0], item[1], item[2].id))
    items = []
    seen_keys: set[tuple[str, str]] = set()
    for score, _name, brand in scored:
        key = (_fold(brand.name), brand.transport_type)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        transport_label = _transport_label(brand.transport_type)
        label = brand.name
        if transport_label:
            same_name = Brand.objects.filter(name__iexact=brand.name).exclude(
                transport_type=brand.transport_type,
            ).exists()
            if same_name:
                label = f'{brand.name} · {transport_label}'
        items.append({
            'id': brand.id,
            'name': brand.name,
            'label': label,
            'country': brand.country.name if brand.country_id else '',
            'transport_type': brand.transport_type,
            'match': 'typo' if score >= 40 else 'name',
        })
        if len(items) >= limit:
            break
    return items


def suggest_models(
    query: str,
    *,
    brand_id: int | None = None,
    brand_name: str = '',
    limit: int = SUGGEST_LIMIT,
) -> list[dict]:
    text = ' '.join(str(query or '').split())
    models = CarModel.objects.select_related('brand', 'brand__country').order_by(
        'name',
        'id',
    )
    if brand_id:
        models = models.filter(brand_id=brand_id)
    elif brand_name.strip():
        models = models.filter(brand__name__iexact=brand_name.strip())
    else:
        return []

    scored: list[tuple[int, str, CarModel]] = []
    for model in models:
        score = _score_name(text, model.name) if text else 50
        if score is None:
            continue
        scored.append((score, model.name.casefold(), model))

    if not text:
        scored = [
            (50, model.name.casefold(), model)
            for model in models[:limit]
        ]

    scored.sort(key=lambda item: (item[0], item[1], item[2].id))
    items = []
    seen: set[tuple[int, str]] = set()
    for _score, _name, model in scored:
        key = (model.brand_id, _fold(model.name))
        if key in seen:
            continue
        seen.add(key)
        items.append({
            'id': model.id,
            'name': model.name,
            'label': model.name,
            'brand_id': model.brand_id,
            'brand_name': model.brand.name,
            'country': model.brand.country.name if model.brand.country_id else '',
            'transport_type': model.transport_type,
        })
        if len(items) >= limit:
            break
    return items


def _load_brand(brand_id) -> Brand | None:
    try:
        pk = int(brand_id)
    except (TypeError, ValueError):
        return None
    return Brand.objects.select_related('country').filter(pk=pk).first()


def _load_model(model_id) -> CarModel | None:
    try:
        pk = int(model_id)
    except (TypeError, ValueError):
        return None
    return CarModel.objects.select_related('brand', 'brand__country').filter(pk=pk).first()


def resolve_vehicle(
    *,
    brand_id=None,
    brand_name: str = '',
    model_id=None,
    model_name: str = '',
) -> VehicleMatch:
    brand_name = ' '.join(str(brand_name or '').split())
    model_name = ' '.join(str(model_name or '').split())
    brand = _load_brand(brand_id)
    model = _load_model(model_id)

    if brand and brand_name and _fold(brand.name) != _fold(brand_name):
        brand = None
    if model and model_name and _fold(model.name) != _fold(model_name):
        model = None
    if brand and model and model.brand_id != brand.id:
        model = None

    if model and not brand:
        brand = model.brand

    ambiguous = False
    country = ''
    transport_type = ''

    if brand:
        brand_name = brand.name
        country = brand.country.name if brand.country_id else ''
        transport_type = brand.transport_type or ''
        brand_id = brand.id
    elif brand_name:
        matches = list(
            Brand.objects.select_related('country').filter(name__iexact=brand_name)
        )
        unique_names = {_fold(item.name) for item in matches}
        unique_types = {item.transport_type for item in matches}
        unique_countries = {
            item.country.name for item in matches if item.country_id
        }
        if len(matches) == 1:
            brand = matches[0]
            brand_id = brand.id
            brand_name = brand.name
            country = brand.country.name if brand.country_id else ''
            transport_type = brand.transport_type or ''
        elif matches and len(unique_names) == 1:
            brand_name = matches[0].name
            if len(unique_countries) == 1:
                country = next(iter(unique_countries))
            if len(unique_types) == 1:
                transport_type = next(iter(unique_types))
            else:
                transport_type = ''
            brand_id = None
            ambiguous = len(unique_types) > 1 or len(unique_countries) > 1
        else:
            brand_id = None
            if len(matches) > 1:
                ambiguous = True
    else:
        brand_id = None

    if model:
        model_name = model.name
        model_id = model.id
        if not transport_type:
            transport_type = model.transport_type or ''
        if not country and model.brand.country_id:
            country = model.brand.country.name
    elif model_name and brand:
        model_matches = list(
            CarModel.objects.filter(brand=brand, name__iexact=model_name)
        )
        if len(model_matches) == 1:
            model = model_matches[0]
            model_id = model.id
            model_name = model.name
            if not transport_type:
                transport_type = model.transport_type or ''
        else:
            model_id = None
            if len(model_matches) > 1:
                ambiguous = True
    else:
        model_id = None

    if transport_type not in ('car', 'truck'):
        transport_type = ''

    return VehicleMatch(
        brand_id=brand_id,
        brand_name=brand_name,
        model_id=model_id,
        model_name=model_name,
        country=country,
        transport_type=transport_type,
        ambiguous=ambiguous,
    )
