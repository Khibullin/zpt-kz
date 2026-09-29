"""High-confidence reject lists for the short homepage request.

These fire only when no explicit part phrase was found. A service word
next to a real part («колодки с установкой») must not land here.
Unknown technical names are intentionally absent: missing from the part
list is not the same as a non-parts request.
"""
from __future__ import annotations

# Whole phrases. «ремонт» alone is not enough: the part must be absent too.
NON_PARTS_PHRASES: tuple[str, ...] = (
    'записаться на сто',
    'ремонт автомобиля',
    'диагностика автомобиля',
    'диагностику автомобиля',
    'диагностика машины',
    'диагностику машины',
    'шиномонтаж',
    'покраска автомобиля',
    'продам автомобиль',
    'куплю автомобиль целиком',
    'куплю автомобиль',
    'рекламное предложение',
    'предлагаем рекламу',
    'продам машину',
    'куплю машину',
    'автокредит',
    'ищу работу',
    'доставка груза',
    'эвакуатор',
    'страхование',
    'вакансия',
    'реклама',
    'кредит',
    'мойка',
    'такси',
)

# A query is meaningless when every remaining word is in this set.
# Checked only after part phrases and non-parts phrases.
MEANINGLESS_TOKENS: frozenset[str] = frozenset({
    'нужно',
    'надо',
    'помогите',
    'есть',
    'цена',
    'сколько',
    'стоит',
    'сломалось',
    'не',
    'работает',
    'срочно',
    'нужна',
    'нужен',
    'нужны',
    'запчасть',
    'деталь',
    'пожалуйста',
})
