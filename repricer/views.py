from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import OuterRef, Subquery
from django.shortcuts import render

from .models import KaspiRepricerRecommendation, KaspiRepricerRule


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

    response = render(
        request,
        "repricer/dashboard.html",
        {
            "rules": rules,
            "summary": summary,
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
