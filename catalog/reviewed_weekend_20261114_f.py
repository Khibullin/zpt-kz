"""Expose exact TRIALLI engine modifications in the existing public fitment table."""
from decimal import Decimal

BATCH = "weekend-20261114-f"
SOURCE_URL = "https://trialli.ru/catalogue/amortizatory-i-opory/amortizatory/ag-03007/"


def display_engine(config):
    modification = str(config.get("modification") or "").strip()
    engine_code = str(config.get("engine_code") or "").strip()
    return " · ".join(part for part in (modification, engine_code) if part)


def stage_review(apps, schema_editor):
    """Populate only the presentation key from already sourced exact fields."""
    if schema_editor.connection.alias != "default":
        return
    from django.db import transaction
    from procurement.analysis import stock_status

    Offer = apps.get_model("procurement", "Offer")
    Fitment = apps.get_model("procurement", "VehicleFitment")
    Listing = apps.get_model("procurement", "RetailListing")
    Activity = apps.get_model("procurement", "Activity")

    counts = {"fitment_pairs": 0, "configuration_rows": 0}
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(pk=25170, supplier_id=5).first()
        listing_ok = Listing.objects.filter(offer_id=25170, product_id=6841, enabled=True).exists()
        if not (
            offer
            and listing_ok
            and offer.brand == "TRIALLI"
            and offer.article == "AG 03007"
            and stock_status(offer.stock) == "in_stock"
            and str(offer.stock) == "4"
            and offer.cost == Decimal("6300.00")
        ):
            return
        fitments = list(Fitment.objects.select_for_update().filter(offer_id=25170).order_by("pk"))
        if len(fitments) != 5 or sum(len(item.configurations or []) for item in fitments) != 11:
            return
        if any(item.source_url != SOURCE_URL or not item.verified for item in fitments):
            return
        if any(config.get("engine") for item in fitments for config in (item.configurations or [])):
            return
        for item in fitments:
            configurations = []
            for existing in item.configurations or []:
                config = dict(existing)
                engine = display_engine(config)
                if not engine:
                    return
                config["engine"] = engine
                configurations.append(config)
            item.configurations = configurations
            item.save(update_fields=["configurations"])
            counts["fitment_pairs"] += 1
            counts["configuration_rows"] += len(configurations)
        Activity.objects.create(user_id=None, action="reviewed_weekend_fitment_display_fix", details={"batch": BATCH, **counts})
