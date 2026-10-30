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
