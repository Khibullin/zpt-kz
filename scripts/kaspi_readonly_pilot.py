from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from integrations.kaspi_competitors import (
    CompetitorPriceSourceError,
    KaspiPublicOfferSource,
)


PILOT_SKUS = [
    "115801437_271928151",
    "136510902_627349511",
    "129914457_677517150",
    "120214535_560663169",
    "131096019_815347049",
    "136896550_140830184",
    "116207063_792647100",
    "835932711",
]


def main() -> int:
    source = KaspiPublicOfferSource(city_id="750000000", max_offers=32)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "city_id": "750000000",
        "products": [],
    }
    success_count = 0

    for index, sku in enumerate(PILOT_SKUS):
        item = {"master_sku": sku}
        try:
            offers = list(source.fetch_offers(master_sku=sku))
        except CompetitorPriceSourceError as exc:
            item["error"] = str(exc)
            print(f"ERROR {sku}: {exc}")
        else:
            success_count += 1
            item["offers"] = [
                {
                    "seller_name": offer.seller_name,
                    "seller_code": offer.seller_code,
                    "price": str(offer.price),
                    "position": offer.position,
                }
                for offer in offers
            ]
            print(f"OK {sku}: {len(offers)} offers")
            for offer in offers:
                print(
                    f"  {offer.position:>2}. {offer.seller_name} "
                    f"[{offer.seller_code or '-'}] — {offer.price}"
                )
        report["products"].append(item)
        if index < len(PILOT_SKUS) - 1:
            time.sleep(1.0)

    Path("kaspi_pilot.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Pilot complete: success={success_count}/{len(PILOT_SKUS)}")
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
