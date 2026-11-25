"""Produce a real Apify Actor input from the latest uploaded ACTIVE.xlsx.

No Apify API call, scraping, automatic repricing or price write occurs here.
This module deliberately keeps the Actor input free of local mapping metadata.
"""
import json
import re

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from procurement.apify_cities import APIFY_CITIES, CITY_NAMES_RU
from .models import KaspiActiveExportRow, KaspiRepricerSearchSettings
from .views import _latest_active_batch


def saved_rules():
    rules, _ = KaspiRepricerSearchSettings.objects.get_or_create(
        pk=1, defaults={
            "cities": ["Almaty", "Astana", "Shymkent"],
            "cards_per_product": 3,
        },
    )
    return rules


def export_units(batch):
    """Keep every ACTIVE row, including SKU unlinked to a ZPT product."""
    return list(
        KaspiActiveExportRow.objects.filter(batch=batch)
        .select_related("listing", "listing__product", "listing__product__brand")
        .order_by("row_number", "pk")
    )



def _identity(row):
    listing = row.listing
    name = (row.product_name or (listing.product.title if listing else "") or "").strip()
    catalog_brand = listing.product.brand.name if listing and listing.product.brand else ""
    listed_brand = (row.brand or "").strip()
    brand = catalog_brand if listed_brand.lower() in ("ag-parts", "ag parts") else (listed_brand or catalog_brand)
    article = (listing.product.article or "").strip() if listing else ""
    article = article or (row.merchant_sku or "").strip()
    if not article:
        codes = [x for x in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", name)
                 if len(x) >= 5 and any(ch.isdigit() for ch in x)]
        article = codes[-1] if codes else ""
    if not article:
        sku = (row.master_sku or "").strip()
        if (re.fullmatch(r"[A-Za-z0-9-]{5,60}", sku)
                and re.search(r"[A-Za-z]", sku) and re.search(r"[0-9]", sku)):
            article = sku
    return name, brand, article


def _queries_for_row(row):
    name, brand, article = _identity(row)
    candidates = []
    if article:
        candidates.append(article)
        if brand:
            candidates.append(f"{brand} {article}")
        if name:
            candidates.append(name if article.casefold() in name.casefold() else f"{name} {article}")
        if "-" in article:
            candidates.append(article.replace("-", ""))
    elif name:
        generic = {"воздушный фильтр", "салонный фильтр", "масляный фильтр",
                   "фильтр", "свеча зажигания"}
        if name.casefold() not in generic:
            candidates.append(f"{brand} {name}" if brand else name)
    queries = []
    seen = set()
    for candidate in candidates:
        candidate = " ".join(candidate.split()).strip()
        if candidate and candidate.casefold() not in seen:
            seen.add(candidate.casefold())
            queries.append(candidate)
    return queries, name, brand, article


def _payload(urls, queries, cities, cards):
    return {
        "startUrls": list(dict.fromkeys(urls)),
        "searchQueries": list(dict.fromkeys(queries)),
        "cities": list(cities), "sortOrder": "relevance",
        "maxItems": 0, "maxItemsPerQuery": cards,
        "includeOffers": True, "maxOffersPerProduct": 0,
        "includeReviews": False,
        "proxyConfiguration": {
            "useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "KZ",
        },
    }


def input_and_mapping(rows, cities, cards):
    """Full search: verified card plus alternate search for every ACTIVE SKU."""
    urls, queries, mapping = [], [], []
    for row in rows:
        listing = row.listing
        url = ((listing.public_url or "").strip().split("?", 1)[0]
               if listing and listing.public_url_verified_at else "")
        searches, name, brand, article = _queries_for_row(row)
        if url:
            urls.append(url)
        queries.extend(searches)
        mapping.append({
            "row_number": row.row_number, "sku": row.master_sku,
            "merchant_sku": row.merchant_sku,
            "title": name, "brand": brand, "article": article,
            "our_price_kzt": row.price, "kaspi_quantity": row.quantity,
            "listing_id": row.listing_id, "match_status": row.status,
            "kind": "url_and_search" if url else "search",
            "input": url or (searches[0] if searches else ""),
            "verified_url": url, "searches": searches,
            "requires_result_verification": True,
            "source_sku_only_fallback": not bool(article),
        })
    return _payload(urls, queries, cities, cards), mapping


def remaining_input_and_mapping(rows, cities, cards):
    """Additional search only for SKUs without a confirmed competitor price."""
    queries, mapping = [], []
    for row in rows:
        if row.competitor_min_price is not None:
            continue
        searches, name, brand, article = _queries_for_row(row)
        queries.extend(searches)
        mapping.append({
            "row_number": row.row_number, "sku": row.master_sku,
            "merchant_sku": row.merchant_sku, "title": name,
            "brand": brand, "article": article, "searches": searches,
            "existing_status": row.competitor_scan_status,
            "requires_result_verification": True,
            "source_sku_only_fallback": not bool(article),
        })
    return _payload([], queries, cities, cards), mapping


def _download(data, filename):
    response = HttpResponse(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
def repricer_rules(request):
    rules = saved_rules()
    batch = _latest_active_batch()
    error = ""
    selected = list(rules.cities)
    cards = rules.cards_per_product
    if request.method == "POST":
        selected = request.POST.getlist("cities")
        cards_raw = request.POST.get("cards_per_product", "")
        if not selected or len(set(selected)) != len(selected) or any(
            city not in APIFY_CITIES for city in selected
        ):
            error = "Выберите города из списка."
        elif cards_raw not in ("1", "2", "3"):
            error = "Количество карточек должно быть 1, 2 или 3."
        if error and request.POST.get("return_to") == "dashboard":
            messages.error(request, error)
            return redirect("repricer:dashboard")
        if not error:
            rules.cities = [city for city in APIFY_CITIES if city in selected]
            rules.cards_per_product = int(cards_raw)
            rules.save(update_fields=["cities", "cards_per_product", "updated_at"])
            # Generate from exactly the selected checkboxes, not stale rules.
            if request.POST.get("action") in ("generate", "generate_remaining"):
                if batch is None:
                    messages.error(request, "Сначала загрузите ACTIVE.xlsx.")
                    return redirect("repricer:dashboard")
                rows = export_units(batch)
                remaining = request.POST.get("action") == "generate_remaining"
                payload, mapping = (
                    remaining_input_and_mapping(rows, rules.cities, rules.cards_per_product)
                    if remaining else input_and_mapping(rows, rules.cities, rules.cards_per_product)
                )
                if not rows or not (payload["startUrls"] or payload["searchQueries"]):
                    messages.error(request, "В ACTIVE.xlsx нет запросов для Apify.")
                    return redirect("repricer:dashboard")
                filename = (
                    f"zpt_apify_remaining_{batch.pk}.json"
                    if remaining else f"zpt_apify_active_{batch.pk}.json"
                )
                return _download(payload, filename)
            messages.success(request, "Правила сохранены.")
            return redirect("repricer:dashboard" if request.POST.get("return_to") == "dashboard" else "repricer:rules")
    rows = export_units(batch) if batch else []
    payload, mapping = input_and_mapping(rows, rules.cities, rules.cards_per_product)
    context = {
        "rules": rules,
        "cities": [
            {"code": code, "name": CITY_NAMES_RU[code], "checked": code in selected}
            for code in APIFY_CITIES
        ],
        "cards": str(cards),
        "error": error,
        "active_batch": batch,
        "row_count": len(rows),
        "url_count": len(payload["startUrls"]),
        "query_count": len(payload["searchQueries"]),
        "unmapped_count": sum(1 for row in rows if row.listing_id is None),
        "remaining_count": sum(row.competitor_min_price is None for row in rows),
        "additional_search_count": len(remaining_input_and_mapping(rows, rules.cities, rules.cards_per_product)[0]["searchQueries"]),
        "actor_url": "https://apify.com/firsthand/kaspi-api",
    }
    response = render(request, "repricer/rules.html", context)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
@require_GET
def repricer_apify_export(request):
    batch = _latest_active_batch()
    if batch is None:
        messages.error(request, "Сначала загрузите ACTIVE.xlsx в разделе «Контроль цен Kaspi».")
        return redirect("repricer:dashboard")
    rules = saved_rules()
    if not rules.cities or any(city not in APIFY_CITIES for city in rules.cities):
        messages.error(request, "Сначала выберите города в разделе «Правила».")
        return redirect("repricer:rules")
    if rules.cards_per_product not in (1, 2, 3):
        messages.error(request, "Некорректное число карточек. Проверьте правила.")
        return redirect("repricer:rules")
    rows = export_units(batch)
    if not rows:
        messages.error(request, "ACTIVE.xlsx не содержит товаров.")
        return redirect("repricer:dashboard")
    remaining = request.GET.get("scope") == "remaining"
    payload, mapping = (
        remaining_input_and_mapping(rows, rules.cities, rules.cards_per_product)
        if remaining else input_and_mapping(rows, rules.cities, rules.cards_per_product)
    )
    if not payload["startUrls"] and not payload["searchQueries"]:
        messages.error(request, "Не удалось сформировать запросы из ACTIVE.xlsx.")
        return redirect("repricer:rules")
    if request.GET.get("format") == "mapping":
        result = {
            "source_filename": batch.filename,
            "batch_id": batch.pk,
            "source_sha256": batch.file_sha256,
            "cities": list(rules.cities),
            "cards_per_product": rules.cards_per_product,
            "own_seller_merchant_id": "30363568",
            "note": (
                "Search candidates require manual identity validation. "
                "After collecting offers, exclude our seller by merchant ID "
                "and take one lowest competitor price across selected cities."
            ),
            "rows": mapping,
        }
        return _download(result, f"zpt_kaspi_mapping_{batch.pk}.json")
    return _download(payload, (
        f"zpt_apify_remaining_{batch.pk}.json" if remaining
        else f"zpt_apify_active_{batch.pk}.json"
    ))
