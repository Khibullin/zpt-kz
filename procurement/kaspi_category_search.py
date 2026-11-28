"""Category-scoped procurement INPUT. No bare SKU or global search fallback."""
import re
from urllib.parse import urlencode

from .kaspi_search_queries import procurement_search_queries

SEARCH_POLICY = 'category_name_article_v6'
PARTS_CATEGORY = 'Replacement parts'
STABILIZER_CATEGORY = 'Anti Roll Bars'


def search_entry(row):
    brand, article = (str(row.get(k) or '').strip() for k in ('brand', 'article'))
    if not brand or not article:
        raise ValueError('Для поиска нужны бренд и полный артикул.')
    category = ' '.join(str(row.get('category') or '').split())
    name = str(row.get('name') or '').strip()
    phrases = procurement_search_queries(brand, article, name)
    phrase = phrases[0] if phrases else ''
    if phrase.casefold() == article.casefold():
        if not category or category.casefold() in ('без категории', 'требует классификации', 'прочее'):
            raise ValueError('Уточните наименование детали перед Apify: ' + brand + ' ' + article)
        phrase = procurement_search_queries(brand, article, category)[0]
    if not phrase or phrase.casefold() == article.casefold():
        raise ValueError('Поиск только по артикулу запрещён: ' + article)
    # Narrow only categories verified in Kaspi's UI. Otherwise keep all parts:
    # a guessed leaf could silently hide the correct, cheaper card.
    target = (STABILIZER_CATEGORY if re.search(r'стабилизатор|anti.?roll|stabiliz', category, re.I)
              else PARTS_CATEGORY)
    url = 'https://kaspi.kz/shop/search/?' + urlencode({
        'text': phrase, 'q': ':category:' + target,
        'sort': 'relevance', 'filteredByCategory': 'false'})
    return {**({'offer_id': row['id']} if 'id' in row else {}), 'query': phrase, 'brand': brand, 'article': article,
            'original_article': article, 'category': target, 'url': url}


def category_mapping(rows):
    seen = {}
    for row in rows:
        entry = search_entry(row)
        seen.setdefault(entry['url'].casefold(), entry)
    return list(seen.values())


def explicit_non_parts_category(value):
    """Reject an explicitly unrelated root, never invent a missing category."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.casefold()
    if any(word in value for word in ('автозапчаст', 'replacement parts', 'anti roll bars')):
        return False
    return any(word in value for word in (
        'красота', 'одежда', 'канцеляр', 'строительство', 'продукты питания',
        'товары для дома', 'бумажные салфетки', 'beauty', 'fashion',
        'office and school', 'construction', 'food', 'paper napkin'))
