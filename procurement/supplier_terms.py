"""Supplier-specific reference terms; never modify quoted costs or profitability."""
from decimal import Decimal
from urllib.parse import urlsplit

OMEGA_BANDS = (
    (0, 0, 'До 500 тыс. ₸'),
    (500000, 5, 'От 500 тыс. до 1 млн ₸'),
    (1000000, 10, 'От 1 до 2,5 млн ₸'),
    (2500000, 11, 'От 2,5 до 5 млн ₸'),
    (5000000, 12, 'От 5 до 10 млн ₸'),
    (10000000, 13, 'От 10 до 15 млн ₸'),
    (15000000, 14, 'От 15 до 20 млн ₸'),
    (20000000, 15, 'От 20 до 25 млн ₸'),
    (25000000, 16, 'От 25 до 30 млн ₸'),
    (30000000, 17, 'Свыше 30 млн ₸'),
)

def supplier_terms(supplier, total=None):
    host = (urlsplit(supplier.website).hostname or '').lower()
    if supplier.name.strip().casefold() not in {'omega', 'омега'} and host not in {'omega-auto-parts.kz', 'www.omega-auto-parts.kz'}:
        return None
    result = {'bands': OMEGA_BANDS, 'supplier': supplier}
    if total is None:
        return result
    total = Decimal(total)
    current = OMEGA_BANDS[0]
    for band in OMEGA_BANDS[1:]:
        if total > band[0]:
            current = band
    result.update(total=total, rate=current[1], boundary=any(total == b[0] for b in OMEGA_BANDS[1:]))
    next_band = next((b for b in OMEGA_BANDS[1:] if Decimal(b[0]) > total), None)
    if next_band:
        result.update(next_threshold=next_band[0], next_rate=next_band[1], remaining=Decimal(next_band[0])-total)
    return result
