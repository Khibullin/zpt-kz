"""Presentation-only helpers for the public dismantler detail page."""
import re
from django import template

register = template.Library()


def clean_value(value):
    value = str(value or '').strip()
    if value.casefold() in {'multi', 'all', 'none', 'null', '-', '—'}:
        return ''
    return value


def address_key(value):
    return re.sub(r'[\W_]+', '', str(value or '').casefold())


@register.simple_tag
def dismantler_profile(seller, card):
    rows = []
    for label, relation, fallback in (
        ('Категории', 'selected_categories', seller.category),
        ('Марки', 'selected_brands', seller.brand_fk.name if seller.brand_fk else seller.brand),
        ('Модели', 'selected_models', seller.model_fk.name if seller.model_fk else seller.model),
        ('Страны', 'selected_countries', seller.country_fk.name if seller.country_fk else ''),
    ):
        values = [clean_value(item.name) for item in getattr(seller, relation).all()]
        values = list(dict.fromkeys(value for value in values if value))
        if not values:
            value = clean_value(fallback)
            values = [value] if value else []
        if values:
            rows.append({'label': label, 'value': ', '.join(values)})
    schedule = card['schedule']
    groups = []
    day_order = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    for row in schedule:
        index = day_order.index(row['day'])
        if groups and groups[-1]['hours'] == row['hours'] and groups[-1]['last_index'] == index - 1:
            groups[-1]['last'] = row['day']
            groups[-1]['last_index'] = index
        else:
            groups.append({'first': row['day'], 'last': row['day'], 'last_index': index, 'hours': row['hours']})
    for group in groups:
        group['days'] = group['first'] if group['first'] == group['last'] else f"{group['first']}–{group['last']}"
    location = clean_value(seller.market_location)
    if address_key(location) == address_key(card['address']):
        location = ''
    return {'specializations': rows, 'schedule': groups, 'location': location,
            'description': clean_value(seller.directory_description),
            'description_kk': clean_value(seller.directory_description_kk)}
