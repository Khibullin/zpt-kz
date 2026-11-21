from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import OuterRef, Subquery
from django.conf import settings
from .competitor_display import competitor_fresh_minutes, competitor_states_for_listings, overlay_unresolved_mapping
from django.utils import timezone
from datetime import timedelta
from django.shortcuts import render

from catalog.models import ProductKaspiListing

from .models import (
    KaspiCompetitorOfferSnapshot,
    KaspiRepricerRecommendation,
    KaspiRepricerRule,
)
from .services import configured_own_merchants


@staff_member_required
def repricer_dashboard(request):
    latest = KaspiRepricerRecommendation.objects.filter(
        rule_id=OuterRef("pk")
    ).order_by("-created_at")

    rules = list(
        KaspiRepricerRule.objects.select_related("listing__product").annotate(
            latest_action=Subquery(latest.values("action")[:1]),
            latest_current_price=Subquery(latest.values("current_price")[:1]),
            latest_best_competitor_price=Subquery(
                latest.values("best_competitor_price")[:1]
            ),
            latest_recommended_price=Subquery(
                latest.values("recommended_price")[:1]
            ),
            latest_market_position=Subquery(latest.values("market_position")[:1]),
            latest_reason=Subquery(latest.values("reason")[:1]),
            latest_created_at=Subquery(latest.values("created_at")[:1]),
        )
    )

    summary = {
        "total": len(rules),
        "lower": sum(1 for rule in rules if rule.latest_action == "LOWER"),
        "raise": sum(1 for rule in rules if rule.latest_action == "RAISE"),
        "hold": sum(1 for rule in rules if rule.latest_action == "HOLD"),
        "without_recommendation": sum(
            1 for rule in rules if rule.latest_action is None
        ),
    }

    own_ids, own_names = configured_own_merchants()
    public_snapshots = KaspiCompetitorOfferSnapshot.objects.filter(
        source__in=("kaspi_public", "office_collector")
    )
    latest_public_snapshot = public_snapshots.order_by("-captured_at").first()
    fresh_minutes = competitor_fresh_minutes()
    fresh_cutoff = timezone.now() - timedelta(minutes=fresh_minutes)
    fresh_snapshot_count = public_snapshots.filter(captured_at__gte=fresh_cutoff).count()
    active_listing_count = ProductKaspiListing.objects.filter(is_active=True).count()
    collected_listing_count = public_snapshots.values("listing_id").distinct().count()
    city_id = str(getattr(settings, "KASPI_REPRICER_CITY_ID", "") or "750000000")

    listings = list(
        ProductKaspiListing.objects.filter(is_active=True)
        .select_related("product").order_by("product__article", "id")
    )
    states = competitor_states_for_listings([listing.pk for listing in listings])
    monitored = []
    for listing in listings:
        state = overlay_unresolved_mapping(
            states[listing.pk], listing.master_sku or "",
            merchant_sku=listing.merchant_sku or "",
            public_url=listing.public_url or "",
        )
        monitored.append({"listing": listing, "state": state})

    response = render(
        request,
        "repricer/dashboard.html",
        {
            "rules": rules,
            "monitored": monitored,
            "summary": summary,
            "own_merchant_configured": bool(own_ids or own_names),
            "public_snapshot_count": public_snapshots.count(),
            "latest_public_snapshot": latest_public_snapshot,
            "fresh_snapshot_count": fresh_snapshot_count,
            "active_listing_count": active_listing_count,
            "collected_listing_count": collected_listing_count,
            "fresh_minutes": fresh_minutes,
            "collector_city_id": city_id,
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
