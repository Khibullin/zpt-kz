"""Rapido physical PP2 snapshot for 2026-09-18 18:17.

Immutable follow-up to rapido-2026-09-18. Exact Product.article keys.
Does not replace the earlier snapshot.
"""

REFERENCE = 'PP2-RAPIDO-2026-09-18-1817'
NOTE = 'Rapido physical inventory snapshot 2026-09-18 18:17'
SNAPSHOT_DATE = '2026-09-18'
SNAPSHOT_NAME = 'rapido-2026-09-18-1817'

# article -> physical qty on PP2
RAPIDO_PP2_QTY = {
    '234349636': 16,
    '4801012010': 209,
    '6600131687': 14,
    '8025530000': 33,
    '8890649934': 15,
    '1109104XGW02A': 29,
    '151000025AA': 36,
    '151000187AA': 36,
    '301001199AA': 51,
    '8104400XP24BA': 20,
    'C281F2801032601': 36,
    'EM2E-8121211E': 50,
    'F081109111HD': 26,
    'S3010140903': 12,
    'SA2E-8121211E-E1': 59,
    'T151109111': 181,
    'T218107011': 19,
    'X01-90000014': 28,
    'X01-90000059': 31,
    'RF059ZKR': 46,
    '1056025900': 16,
    '1064000180': 15,
    '2032047000': 10,
    '8015012700': 20,
    '8025530500': 26,
    '1017110XED95': 10,
    '1017110XEN01': 6,
    '1109101XGW01A': 39,
    '1109110XP64XA': 5,
    '1109110XP6EXACHS': 24,
    '1109120U8710': 4,
    '1109130U1510': 31,
    '1109130U2400': 11,
    '1109140W5000': 1,
    '1109190CR01': 50,
    '13033898-00': 15,
    '151000079AA': 21,
    '151000151AA': 45,
    '272774M400': 35,
    '301000265AA': 55,
    '8100103XKV08A': 24,
    '8100422XNZ01A': 53,
    '8104102P3010': 26,
    '8104400ASZ08A': 8,
    '8104400XKY28B': 24,
    '8114010U8520': 20,
    'A138107915': 20,
    'CD569F2801032700': 23,
    'D20T0120700': 20,
    'F4J161012030': 16,
    'F4J163707010': 20,
    'FAE1109160': 5,
    'HU71151X': 16,
    'J691109111': 26,
    'M111109111': 2,
    'M118107915': 25,
    'P8104140': 15,
    'PBC1109610': 45,
    'S1010140400': 10,
    'S111F2801031700': 49,
    'X0390000206': 26,
    'ZJPCY5000055': 37,
}

# Independent pre-check vs current ZPT PP2 after rapido-2026-09-18:
# article -> (qty_before, qty_after)
KNOWN_CHANGES = {
    '151000187AA': (37, 36),
    '1109140W5000': (2, 1),
}


def rapido_pp2_before_qty():
    before = dict(RAPIDO_PP2_QTY)
    for article, (qty_before, qty_after) in KNOWN_CHANGES.items():
        if RAPIDO_PP2_QTY[article] != qty_after:
            raise ValueError(f'snapshot_mismatch:{article}')
        before[article] = qty_before
    return before
