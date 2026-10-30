"""Readable public supplier text. Original data is never rewritten or discarded."""
import re

_LABEL = re.compile(r'(Марка авто|Модель|Кузов|Производитель|OEM|Применимость)\s*:', re.I)
_UI = re.compile(r'показать\s+ещ[её]\s*\(\s*\d+\s*\)', re.I)
_HEADERS = re.compile(r'Шасси\s+Годы\s+Двигатель\s+Объ[её]м\s+Топливо\s+КПП\s+Кузов\s+Привод', re.I)

def readable_description(text):
    original = str(text or '').strip()
    if not original:
        return {'original': '', 'summary': '', 'facts': [], 'compatibility': []}
    clean = _UI.sub('', original)
    clean = re.sub(r'[▼►▶]', '\n', clean)
    clean = _HEADERS.sub('', clean)
    summary, separator, rest = clean.partition('Характеристики поставщика:')
    if not separator:
        summary, separator, rest = clean.partition('Применимость')
        if separator:
            rest = 'Применимость' + rest
    facts, compatibility = [], []
    if rest:
        before, sep, after = rest.partition('Применимость')
        matches = list(_LABEL.finditer(before))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(before)
            value = re.sub(r'\s+', ' ', before[match.end():end]).strip(' ,;')
            if value:
                facts.append({'label': match.group(1), 'value': value})
        if sep:
            compatibility = [line.strip(' :') for line in after.splitlines() if line.strip(' :')]
        elif not matches:
            compatibility = [line.strip() for line in rest.splitlines() if line.strip()]
    summary = summary.strip()
    if len(summary) > 700:
        summary = summary[:600].rsplit(' ', 1)[0] + '…'
    return {'original': original, 'summary': summary, 'facts': facts, 'compatibility': compatibility}
