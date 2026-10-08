"""Conservative fallback matching for legacy product fitment text.

Structured Product.car_model / selected_models remain the source of truth.
This module is used only when a selected model has zero structured matches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from catalog.models import CarModel

_CLAUSE_SPLIT_RE = re.compile(r'[\n.;]+')
_WORD_RE = re.compile(r'[0-9A-Za-zА-Яа-яЁё]+', re.UNICODE)
_NEGATIVE_MARKERS = (
    'не подтвержд',
    'не подходит',
    'не примен',
    'не совместим',
    'не входит',
    'не входят',
    'не для',
    'исключ',
    'not confirm',
    'not fit',
    'not compatible',
    'exclude',
)


def _tokens(value: object) -> list[str]:
    return [item.casefold() for item in _WORD_RE.findall(str(value or ''))]


def _contains_negative_marker(value: object) -> bool:
    text = ' '.join(str(value or '').casefold().split())
    return any(marker in text for marker in _NEGATIVE_MARKERS)


def _has_exact_model_tokens(
    value: object,
    *,
    model_tokens: list[str],
    longer_sibling_tokens: list[list[str]],
) -> bool:
    """Match model tokens while refusing prefix matches of longer sibling models.

    Example: selected "CS35" must not match "CS35 Plus".
    """
    haystack = _tokens(value)
    width = len(model_tokens)
    if not haystack or not model_tokens or len(haystack) < width:
        return False

    for start in range(0, len(haystack) - width + 1):
        if haystack[start:start + width] != model_tokens:
            continue
        shadowed = False
        for sibling in longer_sibling_tokens:
            sibling_width = len(sibling)
            if (
                sibling_width > width
                and sibling[:width] == model_tokens
                and haystack[start:start + sibling_width] == sibling
            ):
                shadowed = True
                break
        if not shadowed:
            return True
    return False


def legacy_text_supports_model(
    *,
    title: str,
    compatibility: str,
    model_name: str,
    sibling_model_names: Iterable[str] = (),
) -> bool:
    """True only for an explicit positive model mention.

    Any clause that mentions the model with a negative marker is rejected.
    A positive title/compatibility mention is accepted only when it is not just
    the prefix of a longer sibling model name.
    """
    model_tokens = _tokens(model_name)
    if not model_tokens:
        return False

    longer_siblings = []
    for name in sibling_model_names:
        tokens = _tokens(name)
        if (
            len(tokens) > len(model_tokens)
            and tokens[:len(model_tokens)] == model_tokens
        ):
            longer_siblings.append(tokens)

    combined = '\n'.join(part for part in (title, compatibility) if part)
    clauses = [
        clause.strip()
        for clause in _CLAUSE_SPLIT_RE.split(combined)
        if clause.strip()
    ]

    positive = False
    for clause in clauses:
        if not _has_exact_model_tokens(
            clause,
            model_tokens=model_tokens,
            longer_sibling_tokens=longer_siblings,
        ):
            continue
        if _contains_negative_marker(clause):
            return False
        positive = True

    return positive


def legacy_model_fallback_ids(products, model: CarModel) -> list[int]:
    """Return conservative fallback ids among already-filtered products."""
    sibling_names = list(
        CarModel.objects.filter(brand_id=model.brand_id)
        .exclude(pk=model.pk)
        .values_list('name', flat=True)
    )
    rows = (
        products
        .filter(
            brand_id=model.brand_id,
            car_model__isnull=True,
            selected_models__isnull=True,
        )
        .values('id', 'title', 'compatibility')
        .distinct()
    )
    return [
        row['id']
        for row in rows
        if legacy_text_supports_model(
            title=row['title'],
            compatibility=row['compatibility'],
            model_name=model.name,
            sibling_model_names=sibling_names,
        )
    ]
