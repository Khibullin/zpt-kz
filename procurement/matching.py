"""Conservative supplier/Kaspi identity checks; never infer missing identity."""
import re
import unicodedata


def normalize(value):
    return unicodedata.normalize('NFKC', str(value or '')).casefold().replace('ё', 'е').strip()


def tokens(value):
    return sorted(re.findall(r'[\w]+(?:[.,]\d+)?', normalize(value)))


def compare_identity(offer, title, article, brand):
    # Priority is intentional: article, part name/quantity/side, brand.
    if not normalize(article) or normalize(article) != normalize(offer.article):
        return False, 'Артикул не совпадает или отсутствует'
    def part_name(name):
        for identity in (offer.article, offer.brand):
            name = re.sub(r'(?<!\w)' + re.escape(normalize(identity)) + r'(?!\w)', ' ', normalize(name))
        return tokens(name)
    if not part_name(title) or part_name(title) != part_name(offer.name):
        return False, 'Название, сторона или комплектность требуют проверки'
    if not normalize(brand) or normalize(brand) != normalize(offer.brand):
        return False, 'Бренд не совпадает или отсутствует'
    return True, 'Артикул, название и бренд совпадают'
