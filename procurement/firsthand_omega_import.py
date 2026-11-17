"""Import the authorized 17 Nov 2026 Firsthand run for Omega, without guessing fitment.

This is a one-time, idempotent source import, not a Kaspi scraper. Every
observation remains a dated, city-specific record. Product identity is NOT
automatically certified for purchasing by a brand/article match alone.
"""
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


DATA_FILE = Path(__file__).resolve().parent / "data" / "omega_firsthand_20261117.json"
BATCH_KEY = "firsthand_omega_20261117"
CHECKED_ON = date(2026, 11, 17)


def _normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _kaspi_id(url):
    match = re.search(r"-(\d+)/?$", urlsplit(url or "").path)
    return match.group(1) if match else ""


def import_omega_firsthand(Offer, KaspiSellerSnapshot, *, report=None):
    with DATA_FILE.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    groups = defaultdict(list)
    for row in payload["entries"]:
        groups[str(row["id"])].append(row)

    if len(payload["entries"]) != 123 or len(groups) != 41:
        raise ValueError("Unexpected Firsthand source totals: import stopped.")

    counts = {"found": 0, "updated": 0, "identity_conflicts": 0, "not_found": 0,
              "images": 0, "city_results": 0, "seller_offers": 0}
    for product_id, records in groups.items():
        if len(records) != 3 or {r["city"] for r in records} != {"Almaty", "Astana", "Shymkent"}:
            raise ValueError("Incomplete city observations in Firsthand source.")
        offers = list(Offer.objects.filter(
            supplier__name="Omega", kaspi_url__contains="-" + product_id + "/"
        ))
        offers = [offer for offer in offers if _kaspi_id(offer.kaspi_url) == product_id]
        if len(offers) != 1:
            counts["not_found"] += 1
            continue
        offer = offers[0]
        counts["found"] += 1
        article = _normalized(offer.article)
        brand = _normalized(offer.brand)
        if not article or not brand or not all(
            _normalized(r["brand"]) == brand and article in _normalized(r["title"])
            for r in records
        ):
            counts["identity_conflicts"] += 1
            continue

        issue = ("Товар Омеги: колодки ручника; карточка Kaspi: задние колодки"
                 if offer.article == "40153" else "")
        cities = []
        all_images = []
        for row in records:
            sellers = []
            for seller in row["offers"]:
                if not seller.get("sellerName") or seller.get("price") is None:
                    continue
                sellers.append({
                    "name": seller["sellerName"], "seller_id": str(seller.get("sellerId") or ""),
                    "price": str(seller["price"]), "rating": seller.get("sellerRating"),
                    "reviews": seller.get("sellerReviews"), "url": seller.get("sellerUrl") or "",
                    "delivery_days": seller.get("deliveryDays"),
                    "delivery_cost": seller.get("deliveryCost"),
                    "pickup_available": bool(seller.get("pickupAvailable")),
                })
            counts["seller_offers"] += len(sellers)
            counts["city_results"] += 1
            cities.append({
                "city": row["city"], "city_id": str(row["cityId"]),
                "price": str(row["minPrice"]), "sellers": sellers,
                "complete": bool(sellers) and len(sellers) == len(row["offers"]),
            })
            for url in row.get("images") or []:
                if url.startswith("https://resources.cdn-kaspi.kz/") and url not in all_images:
                    all_images.append(url)
        counts["images"] += len(all_images)
        minimum = min(Decimal(city["price"]) for city in cities)
        saved = dict(offer.characteristics or {})
        previous = saved.get(BATCH_KEY)
        if previous:
            # No loss of provenance if this migration is re-applied manually.
            if previous.get("cities") == cities and previous.get("images") == all_images:
                continue
        saved[BATCH_KEY] = {
            "source": "Firsthand Apify", "source_run_started_at": payload["run_started_at"],
            "checked_on": str(CHECKED_ON), "kaspi_product_id": product_id,
            "kaspi_title": records[0]["title"], "kaspi_url": records[0]["url"],
            "identity_status": "conflict" if issue else "candidate",
            "identity_note": issue or "Бренд и артикул совпадают; исполнение и комплектность требуют проверки",
            "cities": cities, "images": all_images,
            "region_scope": "Almaty, Astana, Shymkent; НЕ весь Казахстан",
        }
        updated = ["characteristics"]
        offer.characteristics = saved
        # A proven observation may update a historical *price*, never identity.
        # Do not overwrite a quote already checked after 17 November.
        if not offer.kaspi_price_checked_on or offer.kaspi_price_checked_on <= CHECKED_ON:
            if offer.kaspi_price is not None:
                saved.setdefault("previous_kaspi_quote_before_firsthand_20261117", {
                    "price": str(offer.kaspi_price),
                    "checked_on": str(offer.kaspi_price_checked_on) if offer.kaspi_price_checked_on else None,
                })
            offer.kaspi_price = minimum
            offer.kaspi_price_checked_on = CHECKED_ON
            updated.extend(["kaspi_price", "kaspi_price_checked_on"])
        # Images are candidates until the exact product variant is verified.
        # Only previously identity-verified products can receive an official image.
        if offer.match_verified and not issue and not offer.photo_url and all_images:
            offer.photo_url = all_images[0]
            updated.append("photo_url")
        offer.save(update_fields=updated)
        # Preserve prior snapshot in characteristics rather than erasing history.
        almaty = next(city for city in cities if city["city"] == "Almaty")
        old = KaspiSellerSnapshot.objects.filter(offer_id=offer.pk).first()
        if old and old.checked_on > CHECKED_ON:
            pass
        else:
            if old and old.checked_on != CHECKED_ON:
                saved = dict(offer.characteristics or {})
                saved.setdefault("previous_kaspi_seller_snapshot_before_firsthand_20261117", {
                    "checked_on": str(old.checked_on), "city": old.city,
                    "source_url": old.source_url, "complete": old.complete,
                    "sellers": old.sellers,
                })
                offer.characteristics = saved
                offer.save(update_fields=["characteristics"])
            KaspiSellerSnapshot.objects.update_or_create(
                offer_id=offer.pk,
                defaults={"source_url": offer.kaspi_url,
                          "city": almaty["city_id"],
                          "checked_on": CHECKED_ON,
                          "complete": almaty["complete"],
                          "sellers": almaty["sellers"]},
            )
        counts["updated"] += 1
    if report:
        report(counts)
    return counts
