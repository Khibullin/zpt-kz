"""Broaden search recall without broadening product identity matching."""


def sku_search_queries(brand, article, supplier_name):
    brand, article = str(brand or '').strip(), str(article or '').strip()
    if not brand or not article:
        return []
    queries = [f'{brand} {article}']
    if supplier_name == 'Phaeton DC':
        queries.append(article)  # Kaspi may store the manufacturer under another label.
        if brand.casefold() in ('parts-mall', 'pmc parts mall', 'parts mall'):
            queries.extend([f'PARTS-MALL {article}', f'PMC PARTS MALL {article}'])
    return list(dict.fromkeys(queries))
