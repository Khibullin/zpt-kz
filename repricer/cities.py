"""Kaspi city IDs for the nationwide price collector.

Source: Kaspi Partner Guide, updated 2026-10-08:
https://guide.kaspi.kz/partner/ru/shop/api/general/q3200

These are the 20 principal cities listed in the official guide, not a
claim to cover every settlement served by Kaspi delivery.
"""
from __future__ import annotations

KASPI_KZ_CITIES = (
    ("471010000", "Актау"),
    ("151010000", "Актобе"),
    ("750000000", "Алматы"),
    ("710000000", "Астана"),
    ("231010000", "Атырау"),
    ("351810000", "Жезказган"),
    ("351010000", "Караганда"),
    ("111010000", "Кокшетау"),
    ("191610000", "Конаев"),
    ("391010000", "Костанай"),
    ("431010000", "Кызылорда"),
    ("551010000", "Павлодар"),
    ("591010000", "Петропавловск"),
    ("632810000", "Семей"),
    ("191010000", "Талдыкорган"),
    ("311010000", "Тараз"),
    ("512610000", "Туркестан"),
    ("271010000", "Уральск"),
    ("631010000", "Усть-Каменогорск"),
    ("511010000", "Шымкент"),
)
CITY_BY_ID = dict(KASPI_KZ_CITIES)


def validate_city_id(raw: object) -> str:
    city_id = str(raw or "").strip()
    if city_id not in CITY_BY_ID:
        raise ValueError("Unknown Kaspi city ID")
    return city_id
