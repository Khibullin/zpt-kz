"""AG Parts: Kaspi is the first source for our own retail prices.

A MANUAL, fail-closed price reconciliation. No Kaspi network calls, no
cron, no repricer writes, no automatic application at startup.
Only seller 30363568 observations from the existing collector may qualify.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from catalog.models import Product, ProductKaspiListing, ProductPriceTier
from repricer.models import KaspiCompetitorOfferSnapshot, KaspiRepricerRule


SELLER_SLUG = "ag-parts"
MERCHANT_ID = "30363568"
SNAPSHOT_MAX_AGE = timedelta(hours=24)
FUTURE_TOLERANCE = timedelta(minutes=1)
PUBLIC_LINK_MAX_AGE = timedelta(days=45)
# This SKU currently has a one-piece ZPT sale unit, while Kaspi quotes
# a 4-piece bundle. Product.price and PP2 must NOT be converted blindly.
FOUR_PACK_ARTICLE = "F4J163707010"
MAX_REVIEW_PRODUCTS = 300

READY = "READY_CHANGE"
ALIGNED = "ALIGNED"
BUNDLE_MIGRATION = "BUNDLE_MIGRATION_REQUIRED"
NO_FRESH_PRICE = "NO_FRESH_KASPI"
AMBIGUOUS = "AMBIGUOUS_LISTINGS"
NO_FLOOR = "NO_WHOLESALE_FLOOR"
UNDER_FLOOR = "KASPI_BELOW_FLOOR"
NOT_FILTER = "SALE_UNIT_REVIEW_REQUIRED"
BAD_PRODUCT = "INVALID_PRODUCT_PRICE"


class PriceReconciliationError(ValueError):
    """Unsafe attempted price update; no partial data may be applied."""


@dataclass(frozen=True)
class PriceDecision:
    product_id: int
    article: str
    zpt_price: int | None
    kaspi_price: int | None
    status: str
    wholesale_floor: int | None
    listing_id: int | None
    master_sku: str
    captured_at: str | None
    reason: str


def _merchant_card(url: str, merchant_id: str) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        p = urlsplit(url)
        q = parse_qs(p.query, keep_blank_values=True)
        return bool(
            p.scheme == "https"
            and p.hostname in ("kaspi.kz", "www.kaspi.kz")
            and p.port in (None, 443)
            and not p.username and not p.password and not p.fragment
            and p.path.startswith("/shop/p/") and p.path != "/shop/p/"
            and q.get("m") == [merchant_id] and q.get("ms") == ["true"]
        )
    except ValueError:
        return False


def _recent(stamp, now, max_age) -> bool:
    return bool(stamp and timezone.is_aware(stamp)
                and -FUTURE_TOLERANCE <= now - stamp <= max_age)


def _positive_kzt(value) -> int | None:
    if value is None:
        return None
    try:
        v = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    if not v.is_finite() or v <= 0 or v != v.to_integral_value():
        return None
    amount = int(v)
    return amount if 0 < amount <= 1_000_000_000 else None


def _base_decision(product, *, status, reason, price=None, floor=None, listing=None, observed_at=None):
    return PriceDecision(
        product_id=product.pk,
        article=product.article,
        zpt_price=product.price,
        kaspi_price=price,
        status=status,
        wholesale_floor=floor,
        listing_id=listing.pk if listing else None,
        master_sku=listing.master_sku if listing else "",
        captured_at=observed_at.isoformat() if observed_at else None,
        reason=reason,
    )


def _floor_for_product(product):
    # Rule from owner: wholesale prices are a lower bound on retail prices.
    # Respect all active B2B tiers and any stronger configured repricer floor.
    tiers = [int(t.price) for t in product.price_tiers.all() if t.is_active]
    rules = []
    for l in product.kaspi_listings.all():
        if not l.is_active:
            continue
        rule = getattr(l, "repricer_rule", None)
        if rule is not None and rule.is_enabled:
            floor = _positive_kzt(rule.min_price)
            if floor is None:
                raise PriceReconciliationError("invalid repricer minimum")
            rules.append(floor)
    values = tiers + rules
    return max(values) if values else None


def _is_regular_filter(product) -> bool:
    # This rule never asserts OEM fitment or physical piece counts. It only
    # recognizes the existing one-SKU ZPT sale unit for filter listings.
    # Spark plugs require a separate pack-aware catalog migration.
    name = (product.title or "").casefold()
    return ("фильтр" in name and "свеч" not in name)


def _latest_offer_for_listing(listing, *, now):
    return (
        KaspiCompetitorOfferSnapshot.objects
        .filter(
            listing_id=listing.pk,
            seller_code=MERCHANT_ID,
            source="office_collector",
        )
        .order_by("-captured_at", "-pk")
        .first()
    )


def evaluate_one(product, *, now=None) -> PriceDecision:
    now = now or timezone.now()
    if product.seller_profile is None or product.seller_profile.slug != SELLER_SLUG:
        raise PriceReconciliationError("not an AG Parts seller product")
    if product.status != "active" or not product.article:
        raise PriceReconciliationError("not an active SKU")
    if _positive_kzt(product.price) != product.price:
        return _base_decision(product, status=BAD_PRODUCT, reason="Missing or invalid ZPT retail price")

    candidates = []
    for listing in product.kaspi_listings.all():
        if not listing.is_active or listing.merchant_sku.strip().casefold() != product.article.strip().casefold():
            continue
        if not listing.public_url_verified_at or not _recent(
            listing.public_url_verified_at, now, PUBLIC_LINK_MAX_AGE
        ):
            continue
        if not _merchant_card(listing.public_url, MERCHANT_ID):
            continue
        offer = _latest_offer_for_listing(listing, now=now)
        if not offer or not _recent(offer.captured_at, now, SNAPSHOT_MAX_AGE):
            continue
        observed = _positive_kzt(offer.price)
        if observed is None or not offer.is_available:
            continue
        candidates.append((listing, observed, offer.captured_at))

    if not candidates:
        return _base_decision(product, status=NO_FRESH_PRICE,
                              reason="No verified current price from our Kaspi seller")
    different = {value for _, value, _ in candidates}
    if len(different) != 1:
        return _base_decision(product, status=AMBIGUOUS,
                              reason="Multiple current Kaspi listings disagree about our price")
    # Latest unique offer wins; an older URL alone never justifies a price.
    listing, proposed, captured_at = sorted(candidates, key=lambda v: (v[2], v[0].pk), reverse=True)[0]
    floor = _floor_for_product(product)

    if product.article.strip().upper() == FOUR_PACK_ARTICLE:
        return _base_decision(
            product, status=BUNDLE_MIGRATION, price=proposed, floor=floor,
            listing=listing, observed_at=captured_at,
            reason="Kaspi 4-plug bundle cannot replace a single-piece ZPT product without stock/order-unit migration",
        )
    if not _is_regular_filter(product):
        return _base_decision(
            product, status=NOT_FILTER, price=proposed, floor=floor,
            listing=listing, observed_at=captured_at,
            reason="SKU packaging not verified for safe price unit synchronization",
        )
    if product.price == proposed:
        return _base_decision(
            product, status=ALIGNED, price=proposed, floor=floor,
            listing=listing, observed_at=captured_at,
            reason="Already matches our observed Kaspi price",
        )
    if floor is None:
        return _base_decision(
            product, status=NO_FLOOR, price=proposed,
            listing=listing, observed_at=captured_at,
            reason="Wholesale minimum missing; must be approved before changing price",
        )
    if proposed < floor:
        return _base_decision(
            product, status=UNDER_FLOOR, price=proposed, floor=floor,
            listing=listing, observed_at=captured_at,
            reason="Our Kaspi price is lower than the approved wholesale minimum",
        )
    return _base_decision(
        product, status=READY, price=proposed, floor=floor,
        listing=listing, observed_at=captured_at,
        reason="Exact active SKU and merchant, current price, and wholesale floor verified",
    )


def price_review(*, now=None, lock=False):
    now = now or timezone.now()
    qs = (
        Product.objects
        .filter(seller_profile__slug=SELLER_SLUG, status="active")
        .select_related("seller_profile")
        .order_by("pk")
        .prefetch_related(
            Prefetch("kaspi_listings",
                     queryset=ProductKaspiListing.objects.order_by("pk")
                     .select_related("repricer_rule")),
            Prefetch("price_tiers", queryset=ProductPriceTier.objects.filter(is_active=True)),
        )
    )
    if lock:
        qs = qs.select_for_update()
    products = list(qs[:MAX_REVIEW_PRODUCTS + 1])
    if len(products) > MAX_REVIEW_PRODUCTS:
        raise PriceReconciliationError("too many products; review must be restricted")
    return [evaluate_one(p, now=now) for p in products]


def fingerprint(decisions):
    important = [
        (d.product_id, d.article, d.zpt_price, d.kaspi_price,
         d.listing_id, d.master_sku, d.captured_at, d.wholesale_floor)
        for d in decisions if d.status == READY
    ]
    data = json.dumps(important, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def apply_previewed_prices(*, expected_hash, now=None):
    """Guarded manual operation; NEVER called on app start or schedule.

    Must set AGPARTS_KASPI_PRICE_APPLY_ENABLED=1 and supply hash printed
    immediately before the call. Fully atomic: no silent partial updates.
    """
    if not getattr(settings, "AGPARTS_KASPI_PRICE_APPLY_ENABLED", False):
        raise PriceReconciliationError("manual price application is disabled")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise PriceReconciliationError("full preview fingerprint required")
    with transaction.atomic():
        decisions = price_review(now=now, lock=True)
        changes = [d for d in decisions if d.status == READY]
        if not changes:
            raise PriceReconciliationError("nothing eligible to apply")
        if fingerprint(decisions) != expected_hash:
            raise PriceReconciliationError("live pricing differs from the approved preview")
        for d in changes:
            written = Product.objects.filter(pk=d.product_id, price=d.zpt_price,
                                             seller_profile__slug=SELLER_SLUG).update(
                price=d.kaspi_price, updated_at=timezone.now())
            if written != 1:
                raise PriceReconciliationError("concurrent price mutation; rolled back")
        return changes
