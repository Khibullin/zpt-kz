"""Safely import firsthand/kaspi-api output into the current ACTIVE assortment.

Never write our prices, warehouse data, or Kaspi API. Search-only candidates
without verifiable exact identity do not enter the market minimum.
"""
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from procurement.apify_cities import APIFY_CITIES
from .apify_json import saved_rules
from .models import KaspiActiveCompetitorOffer, KaspiActiveExportRow
from .views import _latest_active_batch

MAX_UPLOAD = 25 * 1024 * 1024
OWN_MERCHANT_IDS = {"30363568"}


def _normalized(value):
    return " ".join(str(value or "").casefold().split())


def _valid_kaspi_url(url):
    if not isinstance(url, str) or len(url) > 500:
        return ""
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in ("kaspi.kz", "www.kaspi.kz"):
        return ""
    if not p.path.startswith("/shop/p/"):
        return ""
    return p._replace(query="", fragment="").geturl()


def _product_id(url):
    url = _valid_kaspi_url(url)
    if not url:
        return ""
    found = re.search(r"-([0-9]+)/?$", urlparse(url).path)
    return found.group(1) if found else ""


def _positive_price(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value() or amount > 999999999:
        return None
    return int(amount)


def _as_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        if isinstance(data.get("offers"), list) and data.get("id"):
            return [data]
    return []


def parse_apify_offers(data, active_rows, cities):
    """Return evidence only for verified product identities.

    A verified Kaspi card ID may come from a previously seller-confirmed URL.
    Search-only candidates need exact title AND trustworthy brand/article match;
    ambiguous candidates are never auto-confirmed.
    """
    allowed = set(cities)
    id_map = defaultdict(list)
    titles = defaultdict(list)
    for row in active_rows:
        listing = row.listing
        if listing and listing.public_url_verified_at:
            identifier = _product_id(listing.public_url)
            if identifier:
                id_map[identifier].append(row)
        name = row.product_name or (listing.product.title if listing else "")
        if name and len(_normalized(name)) >= 12:
            titles[_normalized(name)].append(row)

    evidence = {}
    recognized_row_ids = set()
    not_confirmed = 0
    item_count = 0
    for item in _as_items(data):
        item_count += 1
        if not isinstance(item, dict):
            continue
        city = str(item.get("city") or "").strip()
        if city not in allowed:
            continue
        if str(item.get("currency") or "KZT").upper() != "KZT":
            continue
        url = _valid_kaspi_url(item.get("url"))
        pid = str(item.get("id") or "").strip()
        url_id = _product_id(url)
        if pid and url_id and pid != url_id:
            not_confirmed += 1
            continue
        pid = url_id or pid
        candidates = id_map.get(pid, []) if pid else []
        if len(candidates) != 1:
            candidates = titles.get(_normalized(item.get("title")), [])
            if len(candidates) == 1:
                candidate = candidates[0]
                expected_brand = _normalized(
                    candidate.brand or (
                        candidate.listing.product.brand.name
                        if candidate.listing and candidate.listing.product.brand else ""
                    )
                )
                actual_brand = _normalized(item.get("brand"))
                article = (
                    candidate.listing.product.article if candidate.listing else
                    candidate.merchant_sku
                )
                # Never accept a search result on a title alone.
                article_ok = bool(article and _normalized(article) in _normalized(item.get("title")))
                brand_ok = bool(expected_brand and expected_brand == actual_brand)
                if not (article_ok and brand_ok):
                    candidates = []
            else:
                candidates = []
        if len(candidates) != 1:
            not_confirmed += 1
            continue
        row = candidates[0]
        offers = item.get("offers")
        if not isinstance(offers, list):
            continue
        recognized_row_ids.add(row.pk)
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            seller_id = str(offer.get("sellerId") or "").strip()
            if not seller_id or seller_id in OWN_MERCHANT_IDS:
                continue
            price = _positive_price(offer.get("price"))
            if price is None:
                continue
            key = (row.pk, city, seller_id)
            if key not in evidence or price < evidence[key]["price"]:
                evidence[key] = {
                    "row_id": row.pk, "city": city,
                    "seller_id": seller_id,
                    "seller_name": str(offer.get("sellerName") or "")[:255],
                    "price": price, "url": url,
                }
    return {
        "items": item_count, "recognized": len(recognized_row_ids),
        "recognized_row_ids": recognized_row_ids,
        "unconfirmed": not_confirmed, "evidence": list(evidence.values()),
    }


@staff_member_required
@require_POST
def repricer_output_upload(request):
    batch = _latest_active_batch()
    if not batch:
        messages.error(request, "Сначала загрузите ACTIVE.xlsx.")
        return redirect("repricer:dashboard")
    upload = request.FILES.get("apify_output")
    if not upload or not upload.name.lower().endswith(".json"):
        messages.error(request, "Выберите файл Output JSON из Apify.")
        return redirect("repricer:dashboard")
    if upload.size > MAX_UPLOAD:
        messages.error(request, "Файл Output JSON превышает 25 МБ.")
        return redirect("repricer:dashboard")
    try:
        data = json.loads(upload.read().decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        messages.error(request, "Некорректный JSON. Экспортируйте Apify Dataset в JSON.")
        return redirect("repricer:dashboard")
    items = _as_items(data)
    if not items or len(items) > 30000:
        messages.error(request, "Нет записей Apify или превышен предел 30 000 строк.")
        return redirect("repricer:dashboard")

    rows = list(KaspiActiveExportRow.objects.filter(batch=batch).select_related(
        "listing", "listing__product", "listing__product__brand",
    ))
    selection = saved_rules().cities
    result = parse_apify_offers(data, rows, selection)
    if result["recognized"] == 0:
        messages.error(request, "Не удалось подтвердить ни одну карточку Kaspi. Данные не изменены.")
        return redirect("repricer:dashboard")
    clock = timezone.now()
    evidence = result["evidence"]
    offers_by_row = defaultdict(list)
    for record in evidence:
        offers_by_row[record["row_id"]].append(record)
    with transaction.atomic():
        KaspiActiveCompetitorOffer.objects.filter(active_row__batch=batch).delete()
        for row in rows:
            offers = offers_by_row.get(row.pk, [])
            best = min(offers, key=lambda e: e["price"]) if offers else None
            row.competitor_min_price = best["price"] if best else None
            row.competitor_seller = best["seller_name"] if best else ""
            row.competitor_seller_id = best["seller_id"] if best else ""
            row.competitor_city = best["city"] if best else ""
            row.competitor_url = best["url"] if best else ""
            row.competitor_checked_at = clock if best else None
            row.competitor_scan_status = ("MATCHED" if best else "NO_OFFERS" if row.pk in result["recognized_row_ids"] else "NOT_CONFIRMED")
        KaspiActiveExportRow.objects.bulk_update(rows, [
            "competitor_min_price", "competitor_seller", "competitor_seller_id",
            "competitor_city", "competitor_url", "competitor_checked_at",
            "competitor_scan_status",
        ], batch_size=300)
        KaspiActiveCompetitorOffer.objects.bulk_create([
            KaspiActiveCompetitorOffer(
                active_row_id=e["row_id"], seller_id=e["seller_id"],
                seller_name=e["seller_name"], city=e["city"],
                price=e["price"], product_url=e["url"], checked_at=clock,
            ) for e in evidence
        ], batch_size=500)
    with_prices = len(offers_by_row)
    request.session["repricer_apify_result"] = {
        "filename": upload.name[:120],
        "source_batch_id": batch.pk,
        "rows_in_file": len(items),
        "recognized": result["recognized"],
        "unconfirmed": result["unconfirmed"],
        "with_prices": with_prices,
        "without_prices": len(rows) - with_prices,
        "processed": result["recognized"],
        "not_confirmed_rows": len(rows) - result["recognized"],
        "checked_at": timezone.localtime(clock).strftime("%d.%m.%Y %H:%M"),
    }
    messages.success(
        request, f"Output Apify обработан: {with_prices} товаров с ценами "
        f"конкурентов, {len(rows)-with_prices} без подтверждённых предложений. "
        "Наши цены и остатки не изменены."
    )
    return redirect("repricer:dashboard")


@staff_member_required
@require_POST
def repricer_clear_output(request):
    batch = _latest_active_batch()
    if not batch:
        return redirect("repricer:dashboard")
    rows = list(KaspiActiveExportRow.objects.filter(batch=batch))
    with transaction.atomic():
        KaspiActiveCompetitorOffer.objects.filter(active_row__batch=batch).delete()
        for row in rows:
            row.competitor_min_price = None
            row.competitor_seller = ""
            row.competitor_seller_id = ""
            row.competitor_city = ""
            row.competitor_url = ""
            row.competitor_checked_at = None
            row.competitor_scan_status = ""
        if rows:
            KaspiActiveExportRow.objects.bulk_update(rows, [
                "competitor_min_price", "competitor_seller", "competitor_seller_id",
                "competitor_city", "competitor_url", "competitor_checked_at",
                "competitor_scan_status",
            ])
    request.session.pop("repricer_apify_result", None)
    messages.success(request, "Результаты мониторинга очищены. Наши цены не изменены.")
    return redirect("repricer:dashboard")
