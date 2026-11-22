"""Read-only gate for *new* selections from the permanent buying register.

Existing Selection rows remain untouched. A stored Apify minPrice alone is not
proof of an actual seller offer or a correct vehicle-part execution.
"""
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone

from .models import PermanentBuyingItem, KaspiBuyingCandidate, KaspiPriceAudit
from .omega_policy import POSITIVE_STOCK_PATTERN
import re


def blocked_permanent_offer_ids(offer_ids):
    items = list(PermanentBuyingItem.objects.filter(
        offer_id__in=offer_ids).only(
            'offer_id', 'product_id', 'source', 'identity_status',
            'roi_snapshot', 'kaspi_price_snapshot', 'cost_snapshot',
            'offer__cost', 'offer__stock').select_related('offer'))
    cutoff = timezone.now() - timedelta(days=7)
    candidate_map = {
        c.product_id: c
        for c in KaspiBuyingCandidate.objects.filter(
            product_id__in=[item.product_id for item in items if item.product_id]
        ).only('product_id', 'city_offers', 'imported_at')
    }
    blocked = set()
    # Rechecks are evidence, not authorization. A newer unverified or
    # unprofitable observation invalidates an older permanent snapshot.
    latest_audits = {}
    for audit in KaspiPriceAudit.objects.filter(offer_id__in=offer_ids).order_by(
            'offer_id', '-observed_at', '-pk'):
        latest_audits.setdefault(audit.offer_id, audit)
    for offer_id, audit in latest_audits.items():
        if (audit.observed_at < cutoff or audit.roi < Decimal('50') or audit.identity_status != 'verified'
                or not any(item.offer_id == offer_id and item.kaspi_price_snapshot == audit.min_price
                           for item in items)):
            blocked.add(offer_id)
    for item in items:
        if item.offer_id in blocked:
            continue
        latest = latest_audits.get(item.offer_id)
        confirmed = (item.identity_status == 'verified' or
                     bool(latest and latest.identity_status == 'verified'))
        if (item.source != 'apify'
                or not confirmed
                or item.roi_snapshot < Decimal('50')
                or item.cost_snapshot != item.offer.cost
                or not re.match(POSITIVE_STOCK_PATTERN, str(item.offer.stock or ''))):
            blocked.add(item.offer_id)
            continue
        candidate = candidate_map.get(item.product_id)
        if not candidate or candidate.imported_at < cutoff:
            blocked.add(item.offer_id)
            continue
        prices = []
        for city in candidate.city_offers or []:
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
