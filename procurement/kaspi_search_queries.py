"""Apify recall policy; identity is checked from returned cards, never queries."""
SEARCH_POLICY = 'article_only_v3'
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
