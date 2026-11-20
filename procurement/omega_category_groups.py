"""Presentation-only classifier for supplier-defined Omega price subgroups.

Exact names only: a new or ambiguous supplier category goes to review rather
than being silently guessed from price, name or a broad keyword.
"""
from collections import defaultdict

GROUPS = (
    ('suspension', 'Ходовая и подвеска'),
    ('steering', 'Рулевое управление'),
    ('brakes', 'Тормозная система'),
    ('transmission', 'Трансмиссия и привод'),
    ('engine', 'Двигатель'),
    ('electrical', 'Электрика и электроника'),
    ('cooling', 'Охлаждение и отопление'),
    ('service', 'Расходники и обслуживание'),
    ('body', 'Кузов, оптика и салон'),
    ('small', 'Прочие и мелкие детали'),
    ('review', 'Требует классификации'),
)

# Third level only for the larger suspension group.
SUBGROUPS = (
    ('Амортизаторы, опоры и пружины', (
        'Амортизатор', 'Опора амортизатора', 'Отбойник', 'Пружина',
        'Проставка под пружину', 'Рессора задняя', 'Стремянка задней рессоры',
    )),
    ('Рычаги, шаровые и сайлентблоки', (
        'Рычаг', 'Шаровая опора', 'Сайлентблок', 'Задняя тяга',
    )),
    ('Стабилизаторы', (
        'Стойка стабилизатора', 'Стабилизатор',
    )),
    ('Ступицы и подшипники', (
        'Ступица', 'Подшипник', 'Шпилька колёсная',
    )),
)
EXACT = {
    'steering': (
        'Рулевой наконечник', 'Рулевая тяга', 'Рулевая рейка',
        'Рулевой кардан', 'Рулевая трапеция', 'Сошка рулевая',
        'Маятник рулевой', 'Насос гидроусилителя',
    ),
    'brakes': (
        'Колодки', 'Диск тормозной', 'Барабан тормозной',
        'Суппорт', 'Пружинки на колодки',
    ),
    'transmission': (
        'Шрус (граната)', 'Сепаратор на гранату', 'Комплект сцепление',
        'Корзина сцепления', 'Кулиса', 'Крестовина',
    ),
    'engine': (
        'Вкладыши', 'Гидрокомпенсатор', 'Гильза', 'Клапан',
        'Клапан вентиляции картера', 'Кулачок распредвала',
        'Масляный насос', 'Поршня', 'Распредвал', 'Успокоитель цепи ГРМ',
        'Цепь ГРМ', 'Натяжитель ремня', 'Натяжной механизм',
        'Глушитель', 'Гофра',
    ),
    'electrical': (
        'Бегунок', 'Бендикс', 'Втягивающее реле', 'Генератор',
        'Датчик', 'Диодный мост', 'Дорожка на якорь генератора',
        'Катушка зажигания', 'Кнопка', 'Коммутатор', 'Контактная группа',
        'Лямбда-зонд', 'Переключатель', 'Провода свечные', 'Реле',
        'Стартера', 'Трамблер', 'Шайба стартера', 'Щеткодержатель стартера',
        'Якорь генератора', 'Личинка замка зажигания',
    ),
    'cooling': (
        'Бачок', 'Вентилятор', 'Диффузор', 'Кран печки',
        'Крыльчатка вентилятора', 'Патрубок', 'Помпа', 'Радиатор',
        'Резистор вентилятора салона', 'Реостат печки',
        'Термомуфта вентилятора', 'Термостат',
    ),
    'service': (
        'Надсвечники', 'Ремень', 'Ролик', 'Свеча',
        'Фильтр', 'Щетка стеклоочистителя', 'Щетки',
    ),
    'body': (
        'Брызговик', 'Замок', 'Зацеп замка двери', 'Защита',
        'Крепление', 'Патрон в поворот', 'Петля лючка на бензобак',
        'Подкрылок передний', 'Ручка', 'Стекло фары', 'Стеклоподъемник',
        'Указатель поворота', 'Фара передняя', 'Фара противотуманная',
        'Флажок откр капота', 'Выключатель',
    ),
    'small': (
        'Болт', 'Вилка', 'Втулка', 'Гайка', 'Заглушка', 'Каретка стартера',
        'Кольцо', 'Кронштейн', 'Крышка', 'Муфта', 'Направляющие',
        'Педаль', 'Плунжерная пара', 'Пыльник', 'Резинка',
        'Ремкомплект', 'Сальник', 'Сетка', 'Трос', 'Трубка',
        'Хомут', 'Шайба эксцентриковая', 'Шестерня',
        'Шкив', 'Шланг', 'Штуцер',
    ),
}


def normalize(value):
    return ' '.join(str(value or '').strip().casefold().split())


def _lookup():
    mapping = {}
    for subgroup, names in SUBGROUPS:
        for name in names:
            key = normalize(name)
            if key in mapping:
                raise RuntimeError('Дублируется категория Omega: ' + name)
            mapping[key] = ('suspension', subgroup)
    for group, names in EXACT.items():
        for name in names:
            key = normalize(name)
            if key in mapping:
                raise RuntimeError('Дублируется категория Omega: ' + name)
            mapping[key] = (group, '')
    return mapping


LOOKUP = _lookup()


def group_categories(categories):
    """Return grouped view without changing exact category identities or counts."""
    by_group = {key: {'key': key, 'name': title, 'subgroups': {},
                      'categories': [], 'total': 0, 'previous': 0}
                for key, title in GROUPS}
    for cat in categories:
        group, subgroup = LOOKUP.get(normalize(cat['name']), ('review', ''))
        item = by_group[group]
        item['total'] += 1
        item['previous'] += bool(cat.get('previous_requests'))
        if subgroup:
            item['subgroups'].setdefault(subgroup, []).append(cat)
        else:
            item['categories'].append(cat)
    result = []
    for key, _title in GROUPS:
        group = by_group[key]
        if not group['total']:
            continue
        group['categories'].sort(key=lambda v: (
            not bool(v.get('previous_requests')), normalize(v['name'])
        ))
        # Collapse all suspension subgroups by default, including empty ones.
        group['sections'] = [
            {'name': title, 'categories': sorted(group['subgroups'][title],
                key=lambda v: (not bool(v.get('previous_requests')),
                               normalize(v['name'])))}
            for title, _names in SUBGROUPS
            if title in group['subgroups']
        ] if key == 'suspension' else []
        result.append(group)
    # Already-used groups appear first, then stable taxonomy order.
    order = {key: i for i, (key, _) in enumerate(GROUPS)}
    return sorted(result, key=lambda item: (
        not bool(item['previous']), order[item['key']]
    ))
