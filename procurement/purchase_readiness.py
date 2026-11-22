"""Read-only gate for *new* selections from the permanent buying register.

Existing Selection rows remain untouched. A stored Apify minPrice alone is not
proof of an actual seller offer or a correct vehicle-part execution.
"""
from decimal import Decimal

from .models import PermanentBuyingItem, KaspiBuyingCandidate
from .omega_policy import POSITIVE_STOCK_PATTERN
import re


def blocked_permanent_offer_ids(offer_ids):
    items = list(PermanentBuyingItem.objects.filter(
        offer_id__in=offer_ids).only(
            'offer_id', 'product_id', 'source', 'identity_status',
            'roi_snapshot', 'kaspi_price_snapshot', 'cost_snapshot',
            'offer__cost', 'offer__stock').select_related('offer'))
    candidate_map = {
        c.product_id: c.city_offers
        for c in KaspiBuyingCandidate.objects.filter(
            product_id__in=[item.product_id for item in items if item.product_id]
        ).only('product_id', 'city_offers')
    }
    blocked = set()
    for item in items:
        if (item.source != 'apify'
                or item.identity_status != 'verified'
                or item.roi_snapshot < Decimal('50')
                or item.cost_snapshot != item.offer.cost
                or not re.match(POSITIVE_STOCK_PATTERN, str(item.offer.stock or ''))):
            blocked.add(item.offer_id)
            continue
        prices = []
        for city in candidate_map.get(item.product_id, []) or []:
            if not isinstance(city, dict):
                continue
            for seller in (city.get('sellers') or []):
                if isinstance(seller, dict):
                    try:
                        amount = Decimal(str(seller.get('price')))
                    except (ValueError, ArithmeticError, TypeError):
                        continue
                    if amount.is_finite() and amount > 0:
                        prices.append(amount)
        if not prices or min(prices) != item.kaspi_price_snapshot:
            blocked.add(item.offer_id)
    return blocked
