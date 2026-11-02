"""Public directory presentation; does not participate in request dispatch."""

REPAIR = [
    ('diagnostics', 'Диагностика', ('диагност',)),
    ('engine', 'Двигатель и КПП', ('двигател', 'акпп', 'кпп', 'трансмис', 'мотор')),
    ('brakes', 'Ходовая и тормоза', ('ходов', 'тормоз', 'подвес', 'развал')),
    ('electric', 'Автоэлектрика', ('электр', 'стартер', 'генератор')),
    ('climate', 'Кондиционеры', ('кондиц', 'климат')),
    ('tires', 'Шиномонтаж', ('шиномонт', 'балансиров')),
]
DETAIL = [
    ('polish', 'Полировка', ('полиров',)),
    ('clean', 'Химчистка', ('химчист',)),
    ('ceramic', 'Керамика', ('керамик',)),
    ('film', 'Защитная плёнка', ('антиграв', 'защитн', 'бронепл')),
    ('tint', 'Тонировка', ('тониров',)),
    ('tuning', 'Тюнинг', ('тюнинг', 'автозвук', 'шумоизоляц', 'перетяж', 'оптик')),
]

def catalog_scopes(seller, names):
    # Services are evidence of specialization. An unclassified imported profile
    # does not gain detailing membership without evidence of that specialization.
    text = ' '.join([seller.name, seller.description, *names]).casefold()
    repair = any(word in text for _, _, words in REPAIR for word in words) or 'кузовн' in text or 'ремонт' in text
    detail = any(word in text for _, _, words in DETAIL for word in words) or 'детейлинг' in text or 'мойка' in text
    if seller.seller_type == 'detailing':
        detail = True
    if repair or detail:
        return {kind for kind, found in [('sto', repair), ('detailing', detail)] if found}
    return {'sto'}

def service_directory(request, section=None):
    # The main URL uses the same approved compact directory as explicit scopes.
    kind = {'repair': 'sto', 'detailing': 'detailing'}.get(section, request.GET.get('type', 'sto'))
    if kind not in ('sto', 'detailing'):
        kind = 'sto'
    from .directory_display import repair_directory
    return repair_directory(request, kind)
