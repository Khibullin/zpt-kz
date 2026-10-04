"""Classify a SellerLead from text already stored on the lead.

Does not scrape Instagram, does not call Google or Yandex, and does not create
Brand, CarModel, PartCategory, Seller, User, SellerProfile, or Product rows.
Business type is not taken from the search query alone.
Lifecycle changes only when promote_lifecycle=True (the classify command).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core.models import (
    BUSINESS_TYPE_DISMANTLER,
    BUSINESS_TYPE_MIXED,
    BUSINESS_TYPE_NEW_PARTS,
    BUSINESS_TYPE_UNKNOWN,
    Brand,
    CarModel,
    PartCategory,
    SellerLead,
    SellerLeadDiscoveredBrand,
    SellerLeadDiscoveredCategory,
    SellerLeadDiscoveredModel,
)

DISMANTLER_PHRASES = (
    'авторазбор',
    'авторазборка',
    'разбор автомобилей',
    'разборка автомобилей',
    'б/у запчасти',
    'бу запчасти',
    'контрактные запчасти',
    'запчасти с разбора',
    'used parts',
)
NEW_PARTS_PHRASES = (
    'магазин автозапчастей',
    'новые запчасти',
    'оптовые автозапчасти',
    'автозапчасти в наличии',
    'официальный поставщик',
    'дистрибьютор',
)
GENERIC_ONLY_PHRASES = ('автозапчасти',)
MIN_BRAND_LENGTH = 4
MIN_MODEL_LENGTH = 4
MIN_UNAMBIGUOUS_MODEL_LENGTH = 8
PROMOTABLE_LIFECYCLES = (
    SellerLead.LIFECYCLE_FOUND,
    SellerLead.LIFECYCLE_ENRICHED,
)
CATEGORY_PHRASES = {
    'масляные фильтры': ('масляные фильтры', 'масляный фильтр'),
    'воздушные фильтры': ('воздушные фильтры', 'воздушный фильтр'),
    'тормозные колодки': ('тормозные колодки',),
    'подвеска': ('подвеска',),
    'кузовные детали': ('кузовные детали',),
    'оптика': ('оптика',),
    'двигатели': ('двигатели', 'двигатель'),
    'автоэлектрика': ('автоэлектрика',),
    'масла': ('масла', 'моторные масла'),
}


@dataclass
class TextFragment:
    kind: str
    text: str
    source: object = None
    evidence: object = None


def _fold(value: str) -> str:
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def _phrase_in(phrase: str, text: str) -> bool:
    folded_phrase = _fold(phrase)
    folded_text = _fold(text)
    if len(folded_phrase) < 3 or not folded_text:
        return False
    pattern = r'(?<![\w])' + re.escape(folded_phrase) + r'(?![\w])'
    return re.search(pattern, folded_text) is not None


def _fragments(lead: SellerLead) -> list[TextFragment]:
    rows: list[TextFragment] = []
    evidences = list(
        lead.evidences.select_related('source').order_by('-observed_at', '-pk')[:40]
    )
    for evidence in evidences:
        if evidence.source_id:
            _append_evidence_fragment(rows, evidence)
    for evidence in evidences:
        if not evidence.source_id:
            _append_evidence_fragment(rows, evidence)
    for source in lead.sources.all().order_by('-last_seen_at', '-pk')[:20]:
        text = ' '.join(str(source.display_name or '').split())
        if text:
            rows.append(TextFragment('source', text[:500], source=source))
    for kind, value in (
        ('name', lead.name),
        ('profile', lead.profile_description),
        ('instagram', lead.instagram_username),
        ('website', lead.website_url),
        ('category', lead.category),
        ('car_brands', lead.car_brands),
    ):
        text = ' '.join(str(value or '').split())
        if text:
            rows.append(TextFragment(kind, text[:500]))
    return rows


def _append_evidence_fragment(rows: list[TextFragment], evidence) -> None:
    text = ' '.join(str(evidence.value or '').split())
    if text:
        rows.append(TextFragment('evidence', text[:500], source=evidence.source, evidence=evidence))


def _matching_phrases(phrases: tuple[str, ...], fragments: list[TextFragment]):
    found = []
    for phrase in phrases:
        for fragment in fragments:
            if _phrase_in(phrase, fragment.text):
                found.append((phrase, fragment))
                break
    return found


def _provenance_pair(fragment: TextFragment | None):
    if fragment is None:
        return None, None
    evidence = fragment.evidence
    source = fragment.source
    if evidence is not None and getattr(evidence, 'source_id', None):
        source = evidence.source
    return source, evidence


def _business_type(fragments: list[TextFragment]):
    dismantler = _matching_phrases(DISMANTLER_PHRASES, fragments)
    new_parts = _matching_phrases(NEW_PARTS_PHRASES, fragments)
    generic = _matching_phrases(GENERIC_ONLY_PHRASES, fragments)
    notes = []
    if dismantler and new_parts:
        business_type = BUSINESS_TYPE_MIXED
        confidence = 75
    elif dismantler:
        business_type = BUSINESS_TYPE_DISMANTLER
        confidence = 85
    elif new_parts:
        business_type = BUSINESS_TYPE_NEW_PARTS
        confidence = 85
    else:
        business_type = BUSINESS_TYPE_UNKNOWN
        confidence = 0
    provenance = None
    for phrase, fragment in dismantler + new_parts:
        notes.append(f'{phrase} ({fragment.kind})')
        if provenance is None and (fragment.source is not None or fragment.evidence is not None):
            provenance = fragment
    if generic and business_type == BUSINESS_TYPE_UNKNOWN:
        notes.append('ignored generic: автозапчасти')
    if not notes:
        notes.append('недостаточно явных сигналов')
    return business_type, confidence, '; '.join(notes)[:500], provenance


def _replace_brands(lead: SellerLead, fragments: list[TextFragment], now):
    lead.brand_links.all().delete()
    matches = []
    for brand in Brand.objects.all().order_by('name'):
        name = str(brand.name or '').strip()
        if len(_fold(name)) < MIN_BRAND_LENGTH:
            continue
        for fragment in fragments:
            if _phrase_in(name, fragment.text):
                matches.append((brand, fragment))
                break
    SellerLeadDiscoveredBrand.objects.bulk_create([
        SellerLeadDiscoveredBrand(
            seller_lead=lead,
            brand=brand,
            confidence=80 if len(_fold(brand.name)) >= 5 else 70,
            source=_provenance_pair(fragment)[0],
            evidence=_provenance_pair(fragment)[1],
            source_kind=fragment.kind,
            source_text=fragment.text[:300],
            observed_at=now,
        )
        for brand, fragment in matches
    ])
    return {brand.pk for brand, _fragment in matches}


def _replace_models(lead: SellerLead, fragments: list[TextFragment], brand_ids: set[int], now):
    lead.model_links.all().delete()
    candidates = []
    names_seen: dict[str, int] = {}
    models = list(CarModel.objects.select_related('brand').order_by('name'))
    for car_model in models:
        folded = _fold(car_model.name)
        names_seen[folded] = names_seen.get(folded, 0) + 1
    for car_model in models:
        folded = _fold(car_model.name)
        if len(folded) < MIN_MODEL_LENGTH:
            continue
        brand_ok = car_model.brand_id in brand_ids
        unique_ok = names_seen[folded] == 1 and len(folded) >= MIN_UNAMBIGUOUS_MODEL_LENGTH
        if not brand_ok and not unique_ok:
            continue
        for fragment in fragments:
            if _phrase_in(car_model.name, fragment.text):
                candidates.append((car_model, fragment))
                break
    kept = []
    for car_model, fragment in candidates:
        longer = False
        own = _fold(car_model.name)
        for other, _other_fragment in candidates:
            if other.pk == car_model.pk or other.brand_id != car_model.brand_id:
                continue
            other_name = _fold(other.name)
            if len(other_name) > len(own) and own in other_name:
                longer = True
                break
        if not longer:
            kept.append((car_model, fragment))
    SellerLeadDiscoveredModel.objects.bulk_create([
        SellerLeadDiscoveredModel(
            seller_lead=lead,
            car_model=car_model,
            confidence=80,
            source=_provenance_pair(fragment)[0],
            evidence=_provenance_pair(fragment)[1],
            source_kind=fragment.kind,
            source_text=fragment.text[:300],
            observed_at=now,
        )
        for car_model, fragment in kept
    ])


def _category_names_for_phrase(phrase_names: tuple[str, ...], categories: list[PartCategory]):
    wanted = {_fold(name) for name in phrase_names}
    return [category for category in categories if _fold(category.name) in wanted]


def _replace_categories(lead: SellerLead, fragments: list[TextFragment], now):
    lead.category_links.all().delete()
    categories = list(PartCategory.objects.all().order_by('name'))
    chosen: dict[int, tuple] = {}
    for category in categories:
        if len(_fold(category.name)) < MIN_BRAND_LENGTH:
            continue
        for fragment in fragments:
            if _phrase_in(category.name, fragment.text):
                chosen[category.pk] = (category, fragment, 80)
                break
    for phrase, names in CATEGORY_PHRASES.items():
        matched_categories = _category_names_for_phrase(names, categories)
        if len(matched_categories) != 1:
            continue
        category = matched_categories[0]
        for fragment in fragments:
            if _phrase_in(phrase, fragment.text):
                chosen[category.pk] = (category, fragment, 80)
                break
    SellerLeadDiscoveredCategory.objects.bulk_create([
        SellerLeadDiscoveredCategory(
            seller_lead=lead,
            category=category,
            confidence=confidence,
            source=_provenance_pair(fragment)[0],
            evidence=_provenance_pair(fragment)[1],
            source_kind=fragment.kind,
            source_text=fragment.text[:300],
            observed_at=now,
        )
        for category, fragment, confidence in chosen.values()
    ])


def classify_seller_lead(lead: SellerLead, *, promote_lifecycle: bool = False, dry_run: bool = False) -> str:
    """Store business type and assortment links.

    dry_run returns the type and writes nothing.
    promote_lifecycle moves found/enriched to classified and leaves later statuses.
    A write reloads the row under select_for_update and decides from that row.
    """
    if dry_run:
        fragments = _fragments(lead)
        business_type, _confidence, _evidence, _provenance = _business_type(fragments)
        return business_type
    now = timezone.now()
    with transaction.atomic():
        locked = SellerLead.objects.select_for_update().get(pk=lead.pk)
        fragments = _fragments(locked)
        business_type, confidence, evidence, provenance = _business_type(fragments)
        source, evidence_row = _provenance_pair(provenance)
        locked.business_type = business_type
        locked.business_type_confidence = confidence
        locked.business_type_evidence = evidence
        locked.business_type_source = source
        locked.business_type_evidence_item = evidence_row
        locked.last_classified_at = now
        update_fields = [
            'business_type',
            'business_type_confidence',
            'business_type_evidence',
            'business_type_source',
            'business_type_evidence_item',
            'last_classified_at',
            'updated_at',
        ]
        if promote_lifecycle and locked.lifecycle_status in PROMOTABLE_LIFECYCLES:
            locked.lifecycle_status = SellerLead.LIFECYCLE_CLASSIFIED
            update_fields.append('lifecycle_status')
        locked.save(update_fields=update_fields)
        brand_ids = _replace_brands(locked, fragments, now)
        _replace_models(locked, fragments, brand_ids, now)
        _replace_categories(locked, fragments, now)
    return business_type
