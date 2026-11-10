"""Display names are independent of warehouse and imported original files."""
ASIA_SUPPLIER = 'ТОО Азия Грейт'


def brand_name(value):
    return 'AG Parts' if str(value).strip().casefold().replace('-', '').replace(' ', '') == 'agparts' else value


def supplier_name(value):
    value = str(value or '').strip()
    if value.casefold() in ['omega', 'омега']:
        return 'Омега'
    if value.casefold() in ['азиягрейд', 'азиагрейт', 'азия грейт', 'asia grade', 'asia great', 'тоо «азия грейт»', 'тоо Азия Грейт'.casefold()]:
        return ASIA_SUPPLIER
    return value
