"""Frozen owner-authorized Kaspi card review of 21 October 2026.

Package/URL identity approval only. Runtime prices always come from fresh
seller-specific observations, never from the frozen audit's price field.
"""
import json
from pathlib import Path

AUDITED_OFFERS = json.loads((Path(__file__).parent / "data" / "agparts_kaspi_verified_20261021.json").read_text())["offers"]


def approved_offer_entry(product, entry):
    reviewed = AUDITED_OFFERS.get(product.article)
    if (reviewed is None or reviewed["product_id"] != product.pk
            or entry["kaspi_master_sku"] is not None or entry["kaspi_pack_qty"] is not None):
        return entry
    return {**entry, "kaspi_master_sku": reviewed["master_sku"], "kaspi_pack_qty": reviewed["pack_qty"]}
