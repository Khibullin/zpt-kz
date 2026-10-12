"""Shared model, year and engine applicability for products and maintenance kits."""
import re
from datetime import date

from catalog.applicability import parse_plain_list

ELECTRIC_ENGINE = 'Электро (без ДВС)'
BODY_FITMENT = 'По кузову / системе вентиляции'


def _reviewed_rows(product):
    if not getattr(product, 'fitment_reviewed_on', None):
        return None
    return list(product.fitments.all())


def _fitment_months(row):
    if not row.year_from:
        return set()
    # An open catalogue period stops at the date it was checked. A new year
    # requires a new check, not an automatic promise for future modifications.
    last_year = row.year_to or (row.verified_on.year if row.verified_on else None)
    if not last_year:
        return set()
    last_month = (row.month_to or 12) if row.year_to else row.verified_on.month
    first = row.year_from * 12 + (row.month_from or 1) - 1
    last = last_year * 12 + last_month - 1
    return set(range(first, last + 1))


def _fitment_engine(row):
    if row.engine_policy == 'required':
        return row.engine_code
    if row.engine_policy == 'not_applicable':
        return ELECTRIC_ENGINE
    if row.engine_policy == 'independent':
        return BODY_FITMENT
    return ''


def _structured_year_data(product, model):
    rows = _reviewed_rows(product)
    if rows is None:
        return None
    years, engine_months, wildcard_months, options = set(), {}, set(), set()
    confirmed = [row for row in rows if row.car_model_id == model.pk and row.status == 'confirmed']
    for row in confirmed:
        months = _fitment_months(row)
        years.update(month // 12 for month in months)
        engine = _fitment_engine(row)
        if engine:
            options.add(engine)
            engine_months.setdefault(engine, set()).update(months)
        if row.engine_policy == 'independent':
            wildcard_months.update(months)
    return {
        'years': years,
        'engine_years': {code: {month // 12 for month in months} for code, months in engine_months.items()},
        'engine_months': engine_months,
        'wildcard_months': wildcard_months,
        'wildcard_years': {month // 12 for month in wildcard_months},
        'engine_options': options,
        'engine_independent': any(row.engine_policy == 'independent' for row in confirmed),
        'rows': confirmed,
    }


def _product_fitment_models(product):
    rows = _reviewed_rows(product)
    if rows is not None:
        return list({row.car_model_id: row.car_model for row in rows if row.status == 'confirmed'}.values())
    models = []
    if product.car_model_id:
        models.append(product.car_model)
    models.extend(product.selected_models.all())
    return list({model.pk: model for model in models}.values())


def _model_is_named_in_clause(clause, model, sibling_names):
    labels = (f"{model.brand.name} {model.name}", model.name)
    lowered_clause = clause.casefold()
    for label in labels:
        match = re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", lowered_clause)
        if not match:
            continue
        remainder = lowered_clause[match.end():]
        for sibling_name in sibling_names:
            if sibling_name.casefold() == model.name.casefold():
                continue
            if sibling_name.casefold().startswith(model.name.casefold() + " "):
                suffix = sibling_name[len(model.name):].casefold()
                if remainder.startswith(suffix):
                    break
        else:
            return True
    return False


def _engine_code_is_in_clause(engine, clause):
    clause = clause.upper()
    engine = engine.upper()
    if re.search(rf"(?<![A-Z0-9]){re.escape(engine)}(?![A-Z0-9])", clause):
        return True

    # Expand short suffixes such as SQRE4T15B/C using the stored exact code.
    for shorthand in re.findall(r"\b[A-Z0-9]+/[A-Z]\b", clause):
        base, suffix = shorthand.split("/")
        if base[-1:].isalpha() and engine == base[:-1] + suffix:
            return True
    return False


def _compatibility_clauses(compatibility):
    """Split model fitment from following negative notes without splitting decimals."""
    return [
        clause.strip()
        for clause in re.split(
            r"[;\n]+|(?<=[.!?])\s+(?=[A-ZА-ЯЁ])",
            str(compatibility or ""),
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]


def _explicitly_excludes_model_engine(product, model, engine, sibling_names):
    compatibility = str(getattr(product, "compatibility", "") or "")
    for clause in _compatibility_clauses(compatibility):
        for segment in _model_name_segments(clause, model, sibling_names, include_exclusions=True):
            if _is_exclusion(segment):
                if _engine_code_is_in_clause(engine, segment):
                    return True
    return False


def _model_engine_codes(product, model, sibling_names):
    """Return only codes paired with this model in compatibility text."""
    structured = _structured_year_data(product, model)
    if structured is not None:
        return structured['engine_options']
    codes = parse_plain_list(product.engine_compatibility)
    if not codes:
        return set()

    compatibility = str(getattr(product, "compatibility", "") or "")
    clauses = _compatibility_clauses(compatibility)
    model_clauses = [
        clause for clause in clauses
        if _model_is_named_in_clause(clause, model, sibling_names)
    ]
    if not model_clauses:
        # A single-model product has no cross-model ambiguity.
        if len(_product_fitment_models(product)) == 1:
            return {
                code for code in codes
                if not _explicitly_excludes_model_engine(
                    product, model, code, sibling_names,
                )
            }
        return set()

    compatible = set()
    excluded = set()
    for clause in model_clauses:
        for segment in _model_name_segments(clause, model, sibling_names, include_exclusions=True):
            is_exclusion = _is_exclusion(segment)
            for code in codes:
                if _engine_code_is_in_clause(code, segment):
                    (excluded if is_exclusion else compatible).add(code)
    return compatible - excluded


def _is_exclusion(segment):
    # Qualification (not all engines / not only this article) does not negate
    # the explicitly listed modifications. Direct exclusions remain exclusions.
    cleaned = re.sub(r'\([^)]*\bне\s+все\b[^)]*\)', '', segment, flags=re.IGNORECASE)
    if re.search(r'\bне\s+утверждаем\b.*\bтолько\b', cleaned, re.IGNORECASE):
        return False
    return bool(re.search(r'\b(?:не(?!\s+(?:все|только)\b)|not(?!\s+(?:all|only)\b)|except|excluding)\b', cleaned, re.IGNORECASE))


def _model_name_segments(clause, model, sibling_names, include_exclusions=False):
    """Return text attached to this exact model mention, stopping at the next model."""
    names = sorted(set(sibling_names), key=lambda name: (-len(name), name.casefold()))
    mentions = []
    for name in names:
        pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
        for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
            mentions.append((match.start(), match.end(), name))

    accepted = []
    for start, end, name in sorted(mentions, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < prior_end and end > prior_start for prior_start, prior_end, _ in accepted):
            continue
        accepted.append((start, end, name))
    accepted.sort(key=lambda item: item[0])

    segments = []
    for index, (start, end, name) in enumerate(accepted):
        if name.casefold() != model.name.casefold():
            continue
        prefix_tail = clause[max(0, start - 60):start]
        exclusion = re.search(
            r"\b(?:не|not|except|excluding)(?:\s+\S+){0,3}\s*$",
            prefix_tail,
            flags=re.IGNORECASE,
        )
        if exclusion and not include_exclusions:
            continue
        next_start = accepted[index + 1][0] if index + 1 < len(accepted) else len(clause)
        prefix = exclusion.group(0) if exclusion else ''
        segments.append(prefix + clause[end:next_start])

    target_mention_found = any(
        name.casefold() == model.name.casefold()
        for _start, _end, name in accepted
    )
    if (
        not segments
        and not target_mention_found
        and _model_is_named_in_clause(clause, model, sibling_names)
    ):
        return [clause]
    return segments


_YEAR_FRAGMENT = r"(?:(?:0?[1-9]|1[0-2])\.)?(?:19|20)\d{2}"
_YEAR_RANGE_RE = re.compile(
    rf"(?<!\d)(?P<start>{_YEAR_FRAGMENT})\s*[–—-]\s*(?P<end>{_YEAR_FRAGMENT})(?!\d)",
    re.IGNORECASE,
)
_YEAR_SINCE_RE = re.compile(
    rf"\bс\s+(?P<year>{_YEAR_FRAGMENT})\b",
    re.IGNORECASE,
)


def _year_in_fragment(fragment):
    match = re.search(r"(?:19|20)\d{2}", fragment)
    return int(match.group(0)) if match else None


def _year_ranges_in_text(text):
    """Extract only explicit ranges and ‘since year’ notes from fitment text."""
    ranges = []
    occupied = []
    for match in _YEAR_RANGE_RE.finditer(text):
        first = _year_in_fragment(match.group("start"))
        last = _year_in_fragment(match.group("end"))
        if first and last and first <= last:
            ranges.append((match.start(), match.end(), first, last))
            occupied.append(match.span())

    current_year = date.today().year
    for match in _YEAR_SINCE_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        first = _year_in_fragment(match.group("year"))
        if first:
            ranges.append((match.start(), match.end(), first, max(first, current_year)))

    return ranges


def _engine_mentions_in_text(text, codes):
    mentions = {}
    upper_text = text.upper()
    for code in codes:
        code_upper = code.upper()
        spans = [
            match.span()
            for match in re.finditer(
                rf"(?<![A-Z0-9]){re.escape(code_upper)}(?![A-Z0-9])",
                upper_text,
            )
        ]
        if not spans:
            for match in re.finditer(r"\b[A-Z0-9]+/[A-Z]\b", upper_text):
                base, suffix = match.group(0).split("/")
                if base[-1:].isalpha() and code_upper == base[:-1] + suffix:
                    spans.append(match.span())
        if spans:
            mentions[code] = spans
    return mentions


def _product_model_years(product, model, sibling_names):
    """Return year coverage for a model, plus coverage tied to exact engine codes."""
    structured = _structured_year_data(product, model)
    if structured is not None:
        return structured
    codes = parse_plain_list(product.engine_compatibility)
    compatible_codes = _model_engine_codes(product, model, sibling_names)
    engine_years = {code: set() for code in compatible_codes}
    model_years = set()

    for clause in _compatibility_clauses(getattr(product, "compatibility", "")):
        for segment in _model_name_segments(clause, model, sibling_names):
            ranges = _year_ranges_in_text(segment)
            if not ranges:
                continue

            expanded = set()
            for _start, _end, year_from, year_to in ranges:
                expanded.update(range(year_from, year_to + 1))
            model_years.update(expanded)

            mentions = _engine_mentions_in_text(segment, compatible_codes)
            if not mentions and len(_product_fitment_models(product)) == 1:
                # A single-model listing can keep engine codes in a separate field.
                mentions = {code: [] for code in compatible_codes}
            if not mentions:
                continue

            unique_ranges = {(item[2], item[3]) for item in ranges}
            if len(unique_ranges) == 1:
                year_from, year_to = next(iter(unique_ranges))
                covered = set(range(year_from, year_to + 1))
                for code in mentions:
                    engine_years.setdefault(code, set()).update(covered)
                continue

            positioned_mentions = [
                (code, span)
                for code, spans in mentions.items()
                for span in spans
            ]
            for year_range in ranges:
                preceding = [
                    (code, span)
                    for code, span in positioned_mentions
                    if span[1] <= year_range[0]
                ]
                if preceding:
                    target_codes = {
                        max(preceding, key=lambda item: item[1][1])[0]
                    }
                else:
                    following = [
                        (code, span)
                        for code, span in positioned_mentions
                        if span[0] >= year_range[1]
                    ]
                    target_codes = (
                        {min(following, key=lambda item: item[1][0])[0]}
                        if following else set()
                    )
                for code in target_codes:
                    engine_years.setdefault(code, set()).update(
                        range(year_range[2], year_range[3] + 1)
                    )

    return {
        "years": model_years,
        "engine_years": engine_years,
    }


def product_selection_notices(product, model, year='', engine=''):
    """Display the qualifications that a year-only picker cannot resolve."""
    rows = _reviewed_rows(product)
    if rows is None:
        return []
    notices = []
    for row in rows:
        if model and row.car_model_id != model.pk:
            continue
        if row.status != 'confirmed':
            continue
        if engine and _fitment_engine(row) not in (engine, BODY_FITMENT):
            continue
        if year and int(year) not in {m // 12 for m in _fitment_months(row)}:
            continue
        qualifiers = [v for v in (row.generation, row.body, row.fuel, row.market, f'{row.power_kw} кВт' if row.power_kw else '') if v]
        if qualifiers:
            notices.append('Модификация: ' + ', '.join(qualifiers) + '.')
        if year and ((row.year_from == int(year) and row.month_from and row.month_from != 1) or (row.year_to == int(year) and row.month_to and row.month_to != 12)):
            notices.append('Пограничный год: сверьте месяц выпуска по VIN и период в карточке товара.')
    return list(dict.fromkeys(notices))


