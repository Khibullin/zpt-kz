"""Produce a real Apify Actor input from the latest uploaded ACTIVE.xlsx.

No Apify API call, scraping, automatic repricing or price write occurs here.
This module deliberately keeps the Actor input free of local mapping metadata.
"""
import json

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
            "cities": list(APIFY_CITIES),
            "cards_per_product": 2,
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


def input_and_mapping(rows, cities, cards):
    urls = []
    queries = []
    mapping = []
    for row in rows:
        listing = row.listing
        # Only use URLs previously verified as our merchant card, not numeric
        # SKU values which may not be public Kaspi product ids.
        verified_url = (
            (listing.public_url or "").strip().split("?", 1)[0]
            if listing and listing.public_url_verified_at else ""
        )
        if verified_url:
            kind, target = "url", verified_url
            urls.append(target)
        else:
            name = (row.product_name or (
                listing.product.title if listing else ""
            ) or "").strip()
            brand = (row.brand or (
                listing.product.brand.name
                if listing and listing.product.brand else ""
            ) or "").strip()
            merchant_code = (row.merchant_sku or (
                listing.merchant_sku if listing else ""
            ) or "").strip()
            # The merchant SKU is a useful fallback only if the Kaspi export
            # has no meaningful title; never assume it is a public Kaspi ID.
            target = " ".join(part for part in (brand, name) if part).strip()
            exact_article = (listing.product.article or "").strip() if listing else merchant_code
            if exact_article and exact_article.casefold() not in target.casefold():
                target = (target + " " + exact_article).strip()
            if not target:
                target = merchant_code or row.master_sku
            kind = "search"
            if target:
                queries.append(target)
        mapping.append({
            "row_number": row.row_number,
            "sku": row.master_sku,
            "merchant_sku": row.merchant_sku,
            "title": row.product_name,
            "brand": row.brand,
            "our_price_kzt": row.price,
            "kaspi_quantity": row.quantity,
            "listing_id": row.listing_id,
            "match_status": row.status,
            "kind": kind,
            "input": target,
            "requires_result_verification": kind == "search",
        })
    payload = {
        "startUrls": urls,
        "searchQueries": queries,
        "cities": list(cities),
        "sortOrder": "relevance",
        "maxItems": 0,
        "maxItemsPerQuery": cards,
        "includeOffers": True,
        "maxOffersPerProduct": 0,
        "includeReviews": False,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "KZ",
        },
    }
    # Do not put ZPT-specific fields in the Apify Actor payload. Mapping is
    # downloaded separately for exact SKU reconciliation of actor results.
    return payload, mapping


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
            error = "Выберите от одного до шести городов."
        elif cards_raw not in ("1", "2", "3"):
            error = "Количество карточек должно быть 1, 2 или 3."
        else:
            rules.cities = [city for city in APIFY_CITIES if city in selected]
            rules.cards_per_product = int(cards_raw)
            rules.save(update_fields=["cities", "cards_per_product", "updated_at"])
            messages.success(request, "Правила сохранены. Можно сформировать JSON для Apify.")
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
    payload, mapping = input_and_mapping(rows, rules.cities, rules.cards_per_product)
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
    return _download(payload, f"zpt_apify_active_{batch.pk}.json")
