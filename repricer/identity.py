"""One identity for repricer INPUT, returned cards and price evidence."""
import re
from functools import lru_cache

from procurement.kaspi_search_queries import procurement_search_queries

RETAILER_BRANDS = {'agparts', 'agpartskz'}
NEUTRAL_BRANDS = {'', 'безбренда', 'nobrand'}


def brand_key(value):
    value = ''.join(re.findall(r'[a-z0-9а-яё]+', str(value or '').casefold()))
    return 'partsmall' if value in ('partsmall', 'pmcpartsmall') else value


def article_key(value):
    return re.sub(r'[\s\-\u2010-\u2015\u2212]+', '', str(value or '')).casefold()


@lru_cache(maxsize=4096)
def _article_pattern(article):
    compact = article_key(article)
    return re.compile(r'(?<![\w/.-])' + r'[\s\-\u2010-\u2015\u2212]*'.join(
        map(re.escape, compact)) + r'(?![\w/.-])', re.I) if compact else None


def article_in_title(article, title):
    pattern = _article_pattern(str(article or ''))
    return bool(pattern and pattern.search(str(title or '')))


def identity(row):
    listing = row.listing
    product = listing.product if listing else None
    name = str(row.product_name or (product.title if product else '') or '').strip()
    catalog_brand = product.brand.name if product and product.brand else ''
    brand = str(row.brand or '').strip()
    if brand_key(brand) in RETAILER_BRANDS | NEUTRAL_BRANDS:
        brand = catalog_brand if brand_key(catalog_brand) not in RETAILER_BRANDS else ''
    article = str(product.article or '').strip() if product else ''
    merchant = str(row.merchant_sku or '').strip()
    # A shop's internal SKU is not automatically a manufacturer article.
    if not article and merchant and article_in_title(merchant, name):
        article = merchant
    if not article:
        codes = [token for token in re.findall(r'[A-Za-z0-9]+(?:[-/\.][A-Za-z0-9]+)*', name)
                 if 4 <= len(token) <= 60 and any(ch.isdigit() for ch in token)
                 and not re.fullmatch(r'(?:19|20)\d{2}(?:-(?:19|20)\d{2})?', token)]
        article = codes[-1] if codes else ''
    if not article:
        sku = str(row.master_sku or '').strip()
        if re.fullmatch(r'[A-Za-z0-9./-]{5,60}', sku) and re.search('[A-Za-z]', sku) and re.search('[0-9]', sku):
            article = sku
    if product and article and article_key(name) == article_key(article):
        catalog_name = str(product.title or '').strip()
        if article_in_title(article, catalog_name) and article_key(catalog_name) != article_key(article):
            name = catalog_name
    return name, brand, article


def execution_conflict(left, right):
    """Reject explicit side, axle and quantity contradictions; unknown stays unknown."""
    def facts(text):
        text = str(text or '').casefold()
        side = {key for key, pattern in (
            ('left', r'\bлев\w*|\bleft\b'), ('right', r'\bправ\w*|\bright\b')) if re.search(pattern, text)}
        axle = {key for key, pattern in (
            ('front', r'перед|\bпер\.|\bfront\b'), ('rear', r'задн|\bзад\.|\brear\b')) if re.search(pattern, text)}
        number = re.search(r'(?<!\d)(\d+)\s*(?:шт\.?|pcs?\b|pieces?\b)', text)
        pack = int(number.group(1)) if number else None
        is_set = bool(re.search(r'комплект|набор|\bset\b|\bkit\b', text))
        return side, axle, pack, is_set
    a, b = facts(left), facts(right)
    return (any(x and y and x.isdisjoint(y) for x, y in zip(a[:2], b[:2]))
            or (a[2] is not None and b[2] is not None and a[2] != b[2])
            or (a[2] == 1 and b[3]) or (b[2] == 1 and a[3]))


def queries_for_row(row):
    name, brand, article = identity(row)
    if not article:
        return [], name, brand, article
    queries = procurement_search_queries(brand, article, name)
    # No standalone article, even if the source description is incomplete.
    return ([queries[0]] if queries and article_key(queries[0]) != article_key(article) else []), name, brand, article
