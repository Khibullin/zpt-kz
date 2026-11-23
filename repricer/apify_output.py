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


def _part_codes(title):
    """Distinctive article-shaped tokens; bare numerical Kaspi SKU is not a part code."""
    codes = set()
    for value in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", str(title or "").upper()):
        if len(value) < 5 or len(value) > 50 or not any(ch.isdigit() for ch in value):
            continue
        if value.isdigit() and len(value) == 4 and value.startswith(("19", "20")):
            continue
        codes.add(value)
    return codes


def _brand_normalized(value):
    return "".join(re.findall(r"[a-z0-9а-яё]+", str(value or "").casefold()))


def _source_codes(row):
    listing = row.listing
    name = row.product_name or (listing.product.title if listing else "")
    codes = _part_codes(name)
    if listing and listing.product.article:
        codes.update(_part_codes(listing.product.article))
    if row.merchant_sku:
        codes.update(_part_codes(row.merchant_sku))
    # Alphanumeric source SKU may be a part article, numeric SKU may be
    # a Kaspi merchant identifier and cannot prove article equality.
    sku = (row.master_sku or "").strip()
    if re.fullmatch(r"[A-Za-z0-9-]{5,50}", sku) and any(ch.isalpha() for ch in sku):
        codes.update(_part_codes(sku))
    return codes


def _search_match(row, item):
    """Require an exact distinctive article and corroborating brand, if known."""
    title = str(item.get("title") or "")
    found_codes = _part_codes(title)
    expected_codes = _source_codes(row)
    # Beware of title dates/variants: matching a distinctive part code is
    # necessary; a common category title alone is never enough.
    matched_codes = expected_codes & found_codes
    if not matched_codes:
        return False
    brand = _brand_normalized(
        row.brand or (
            row.listing.product.brand.name
            if row.listing and row.listing.product.brand else ""
        )
    )
    if brand:
        item_brand = _brand_normalized(item.get("brand"))
        item_title = _brand_normalized(title)
        if not (brand == item_brand or brand in item_title):
            return False
    return True


def parse_apify_offers(data, active_rows, cities):
    """Map Apify offers to a verified card or a uniquely confirmed part article.

    Preserve mismatch/empty-offer causes for each ACTIVE row. Unverified
    search candidates never set a price, and own offers never compete.
    """
    allowed = set(cities)
    verified_ids = defaultdict(list)
    source_id_hints = defaultdict(list)
    code_index = defaultdict(list)
    for row in active_rows:
        listing = row.listing
        if listing and listing.public_url_verified_at:
            pid = _product_id(listing.public_url)
            if pid:
                verified_ids[pid].append(row)
        # A numeric Kaspi SKU prefix is a candidate hint, not proof by itself.
        prefix = (row.master_sku or "").split("_", 1)[0]
        if prefix.isdecimal() and len(prefix) >= 8:
            source_id_hints[prefix].append(row)
        for code in _source_codes(row):
            code_index[code].append(row)

    evidence = {}
    verified_rows = set()
    unconfirmed = 0
    skipped_city = 0
    offer_counters = defaultdict(lambda: defaultdict(int))
    item_count = 0
    for item in _as_items(data):
        item_count += 1
        if not isinstance(item, dict):
            continue
        city = str(item.get("city") or "").strip()
        if city not in allowed:
            skipped_city += 1
            continue
        if str(item.get("currency") or "KZT").upper() != "KZT":
            continue
        url = _valid_kaspi_url(item.get("url"))
        pid = str(item.get("id") or "").strip()
        url_id = _product_id(url)
        if pid and url_id and pid != url_id:
            unconfirmed += 1
            continue
        pid = url_id or pid

        rows = verified_ids.get(pid, []) if pid else []
        if len(rows) != 1:
            # Candidate hints must agree with a distinctive part article,
            # and known brand, not merely the numeric merchant SKU.
            candidates = set()
            title_codes = _part_codes(item.get("title"))
            for code in title_codes:
                for row in code_index.get(code, []):
                    candidates.add(row.pk)
            by_id = source_id_hints.get(pid, []) if pid else []
            if by_id:
                by_id_pks = {row.pk for row in by_id}
                candidates &= by_id_pks
            rows = [
                row for row in active_rows
                if row.pk in candidates and _search_match(row, item)
            ]
        if len(rows) != 1:
            unconfirmed += 1
            continue
        row = rows[0]
        offers = item.get("offers")
        if not isinstance(offers, list):
            offer_counters[row.pk]["missing_offers_array"] += 1
            verified_rows.add(row.pk)
            continue
        verified_rows.add(row.pk)
        if not offers:
            offer_counters[row.pk]["empty_offers"] += 1
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            offer_counters[row.pk]["raw_offers"] += 1
            seller_id = str(
                offer.get("sellerId") or offer.get("merchantId") or ""
            ).strip()
            if not seller_id:
                offer_counters[row.pk]["missing_seller_id"] += 1
                continue
            if seller_id in OWN_MERCHANT_IDS:
                offer_counters[row.pk]["own"] += 1
                continue
            price = _positive_price(offer.get("price"))
            if price is None:
                offer_counters[row.pk]["invalid_price"] += 1
                continue
            key = (row.pk, city, seller_id)
            if key not in evidence or price < evidence[key]["price"]:
                evidence[key] = {
                    "row_id": row.pk, "city": city, "seller_id": seller_id,
                    "seller_name": str(offer.get("sellerName") or "")[:255],
                    "price": price, "url": url,
                }
            offer_counters[row.pk]["valid"] += 1

    return {
        "items": item_count,
        "recognized": len(verified_rows),
        "recognized_row_ids": verified_rows,
        "unconfirmed": unconfirmed,
        "skipped_city": skipped_city,
        "evidence": list(evidence.values()),
        "offer_counters": {k: dict(v) for k, v in offer_counters.items()},
    }


def _missing_reason(row_id, recognized_ids, counters):
    if row_id not in recognized_ids:
        return "Карточка не подтверждена по артикулу и бренду"
    state = counters.get(row_id, {})
    if state.get("missing_offers_array"):
        return "Apify не вернул поле offers"
    if state.get("empty_offers"):
        return "Apify вернул пустой список offers"
    if state.get("missing_seller_id"):
        return "У предложений отсутствует идентификатор продавца"
    if state.get("invalid_price"):
        return "У предложений некорректная или пустая цена"
    if state.get("own") and not state.get("valid"):
        return "Только предложения нашего продавца AG Parts"
    return "Нет подтверждённых предложений других продавцов"


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
            row.competitor_scan_note = "" if best else _missing_reason(
                row.pk, result["recognized_row_ids"], result["offer_counters"]
            )
        KaspiActiveExportRow.objects.bulk_update(rows, [
            "competitor_min_price", "competitor_seller", "competitor_seller_id",
            "competitor_city", "competitor_url", "competitor_checked_at",
            "competitor_scan_status", "competitor_scan_note",
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
        "reason_counts": dict(__import__("collections").Counter(
            _missing_reason(row.pk, result["recognized_row_ids"], result["offer_counters"])
            for row in rows if row.pk not in offers_by_row
        )),
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
            row.competitor_scan_note = ""
        if rows:
            KaspiActiveExportRow.objects.bulk_update(rows, [
                "competitor_min_price", "competitor_seller", "competitor_seller_id",
                "competitor_city", "competitor_url", "competitor_checked_at",
                "competitor_scan_status", "competitor_scan_note",
            ])
    request.session.pop("repricer_apify_result", None)
    messages.success(request, "Результаты мониторинга очищены. Наши цены не изменены.")
    return redirect("repricer:dashboard")
