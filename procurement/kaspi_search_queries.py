"""Apify recall policy; identity is checked from returned cards, never queries."""
SEARCH_POLICY = 'name_article_v4'
MAX_ITEMS_PER_QUERY = 20


def sku_search_queries(brand, article, supplier_name, *, legacy=False):
    brand, article = str(brand or '').strip(), str(article or '').strip()
    if not brand or not article:
        return []
    if not legacy:
        return [article]
    # Existing saved requests keep their historical mapping and batch counts.
    queries = [f'{brand} {article}']
    if supplier_name == 'Phaeton DC':
        queries.append(article)
        if brand.casefold() in ('parts-mall', 'pmc parts mall', 'parts mall'):
            queries.extend([f'PARTS-MALL {article}', f'PMC PARTS MALL {article}'])
    return list(dict.fromkeys(queries))

def monitoring_search_queries(brand, article, name=''):
    """Search broadly; the result's own brand/full article must still match.

    Keep the immutable legacy builder above for previously saved requests.
    Removing a brand here never makes different manufacturers equivalent.
    """
    import re
    brand, article, name = (str(x or '').strip() for x in (brand, article, name))
    if not article:
        return []
    brands = [brand] if brand else []
    if brand.casefold() in ('parts-mall', 'pmc parts mall', 'parts mall'):
        brands = ['PMC PARTS MALL', 'PARTS-MALL', 'PARTS MALL']
    for label in sorted(brands, key=len, reverse=True):
        name = re.sub(r'(?<!\w)' + re.escape(label) + r'(?!\w)', ' ', name, flags=re.I)
    # Do not cut suffixes, strip zeros, or derive an article from a search phrase.
    name = re.sub(r'(?<![\w/.-])' + re.escape(article) + r'(?![\w/.-])', ' ', name, flags=re.I)
    name = ' '.join(name.split()).strip(' ,;:')
    return list(dict.fromkeys(filter(None, [f'{name} {article}'.strip(), article])))
