"""Exact, stored Kazakh translations; never modify supplier product records."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def product_title_translations():
    path = Path(__file__).parent / 'data' / 'kazakh_product_titles.json'
    return json.loads(path.read_text(encoding='utf-8'))


def kazakh_product_title(title):
    return product_title_translations().get(str(title or '').strip(), '')


@lru_cache(maxsize=1)
def description_translations():
    path = Path(__file__).parent / 'data' / 'kazakh_product_descriptions.json'
    return json.loads(path.read_text(encoding='utf-8'))['entries']


def kazakh_description_summary(source_text, summary):
    """Only show the saved translation of this exact source version.

    The digest is a non-security content key. Compare the readable source as
    well, so an edited supplier description never receives an outdated text.
    """
    import hashlib
    source = str(source_text or '')
    digest = hashlib.md5(source.encode('utf-8'), usedforsecurity=False).hexdigest()
    entry = description_translations().get(digest)
    if not entry:
        return ''
    normalized = str(summary or '').strip().replace('\r\n', '\n')
    if entry.get('ru_summary') != normalized:
        return ''
    return entry.get('kk_summary', '')
