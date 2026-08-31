"""Approved AG Parts stage-1 air-filter wholesale assortment."""

from __future__ import annotations

AG_PARTS_SLUG = 'ag-parts'

# Existing approved wholesale SKU set from catalog.migrations.0026.
ORIGINAL_WHOLESALE_ARTICLES = frozenset({
    '1017110XED95',
    '1017110XEN01',
    '13033898-00',
    '272774M400',
    '301000265AA',
    '301001199AA',
    '4801012010',
    '8100103XKV08A',
    '8100422XNZ01A',
    '8104102P3010',
    '8104400ASZ08A',
    '8104400XKY28B',
    '8104400XP24BA',
    '8114010U8520',
    '8890649934',
    'C281F2801032601',
    'CD569F2801032700',
    'D20T0120700',
    'EM2E-8121211E',
    'F4J161012030',
    'F4J163707010',
    'HU71151X',
    'RF059ZKR',
    'S111F2801031700',
    'T218107011',
    'X0390000206',
})

# article: (retail, wholesale)
STAGE1_AIR_FILTERS = {
    '1109101XGW01A': (1150, 610),
    '1109104XGW02A': (1400, 650),
    '1109110XP6EXACHS': (2640, 720),
    '1109110XP64XA': (2640, 650),
    '1109120U8710': (2750, 370),
    '1109130U2400': (3800, 630),
    '1109140W5000': (4180, 840),
    '1109190CR01': (2750, 650),
    '151000025AA': (1050, 530),
    '151000079AA': (1000, 650),
    '151000151AA': (2950, 620),
    '151000187AA': (2200, 790),
    '2032047000': (4190, 680),
    '6600131687': (2397, 630),
    'FAE1109160': (1760, 310),
    'J691109111': (1980, 520),
    'M111109111': (1540, 470),
    'S1010140400': (1760, 500),
    'S3010140903': (890, 670),
    'T151109111': (1700, 410),
    'X01-90000014': (985, 610),
}

STAGE1_AIR_FILTER_ARTICLES = frozenset(STAGE1_AIR_FILTERS)

# article: retail, wholesale, title, compatibility, oem_cross_references
STAGE2_AIR_FILTERS = {
    '1064000180': {
        'retail': 1800,
        'wholesale': 330,
        'title': 'Воздушный фильтр 1064000180',
        'compatibility': '',
        'oem_cross_references': (
            'AG 302 ECO; SA 8147; A1003; A-1180; SB 3250; 71-01286-SX'
        ),
    },
    '1109130U1510': {
        'retail': 2099,
        'wholesale': 440,
        'title': 'Воздушный фильтр JAC S5 — 1109130U1510',
        'compatibility': 'JAC S5',
        'oem_cross_references': '',
    },
    'F081109111HD': {
        'retail': 3300,
        'wholesale': 710,
        'title': (
            'Воздушный фильтр Jetour X70 / Dashing / X90 Plus — F081109111HD'
        ),
        'compatibility': 'Jetour X70, Dashing, X90 Plus; 2022–2025',
        'oem_cross_references': '',
    },
}

STAGE2_AIR_FILTER_ARTICLES = frozenset(STAGE2_AIR_FILTERS)
APPROVED_AIR_FILTER_ARTICLES = STAGE1_AIR_FILTER_ARTICLES | STAGE2_AIR_FILTER_ARTICLES

PHOTO_ALIASES = {
    'J61109111': 'J691109111',
}

EXCLUDED_ARTICLES = frozenset({
    '1109110XKV08A',
    'PBC1109610',
    '1109110XP6EXA',
})

LEGACY_NULL_DUPLICATES = (
    {'article': '1109190CR01'},
    {'article': '151000025AA', 'legacy_price': 1150},
    {'article': 'S1010140400', 'legacy_price': 1680},
)


def article_key(article):
    return (article or '').strip()


def resolve_photo_article(folder_name):
    key = article_key(folder_name)
    if not key:
        return ''
    return PHOTO_ALIASES.get(key, PHOTO_ALIASES.get(key.upper(), key))
