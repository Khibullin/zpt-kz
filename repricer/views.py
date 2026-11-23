"""Staff-only manual Kaspi price control dashboard and product details.

Price edits are saved as drafts only; no API push or automatic repricing.
"""
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from catalog.models import ProductKaspiListing, CatalogImportBatch
from .cities import KASPI_KZ_CITIES, CITY_BY_ID
from .competitor_display import competitor_fresh_minutes, competitor_states_for_listings, overlay_unresolved_mapping
from .models import KaspiCompetitorIngestBatch, KaspiCompetitorOfferSnapshot, KaspiManualCollectionRun, KaspiManualPriceDraft, KaspiActiveExportRow
from .services import configured_own_merchants

PUBLIC_SOURCES = ("kaspi_public", "office_collector")


def _filtered_listings(request):
    qs = ProductKaspiListing.objects.filter(is_active=True).select_related(
        "product", "product__brand", "product__category"
    ).order_by("product__article", "pk")
    query = (request.GET.get("q") or "").strip()[:120]
    supplier = (request.GET.get("supplier") or "").strip()
    if query:
        qs = qs.filter(Q(product__article__icontains=query) |
                       Q(product__title__icontains=query) |
                       Q(product__brand__name__icontains=query) |
                       Q(master_sku__icontains=query) |
                       Q(merchant_sku__icontains=query))
    if supplier:
        qs = qs.filter(product__supplier=supplier)
    return qs, query, supplier


def _listing_state(listing, states):
    return overlay_unresolved_mapping(
        states[listing.pk], listing.master_sku or "",
        merchant_sku=listing.merchant_sku or "",
        public_url=listing.public_url or "",
    )


def _row(listing, state, city=""):
    draft = getattr(listing, "manual_price_draft", None)
    our = listing.last_known_our_price
    best = state.best_price
    return {
        "listing": listing, "state": state, "city": city,
        "draft": draft, "our": our,
        "delta": (our - best) if our is not None and best is not None else None,
    }



def _latest_active_batch():
    """Only completed full imports with a complete row ledger become authoritative."""
    return (
        CatalogImportBatch.objects.filter(
            source=CatalogImportBatch.SOURCE_KASPI_LISTING_FACTS,
            source_scope=CatalogImportBatch.SCOPE_FULL,
            status=CatalogImportBatch.STATUS_SUCCESS,
            repricer_active_rows__isnull=False,
        ).distinct().order_by("-finished_at", "-pk").first()
    )


def _active_assortment(request, batch):
    """Paginated ACTIVE rows including unmapped SKUs and their original prices."""
    qs = KaspiActiveExportRow.objects.filter(batch=batch).select_related(
        "listing", "listing__product", "listing__product__brand",
        "listing__product__category", "listing__manual_price_draft",
    ).order_by("row_number", "pk")
    search = (request.GET.get("q") or "").strip()[:120]
    supplier = (request.GET.get("supplier") or "").strip()
    city = (request.GET.get("city") or "").strip()
    status = (request.GET.get("status") or "").strip()
    if search:
        qs = qs.filter(
            Q(master_sku__icontains=search)
            | Q(merchant_sku__icontains=search)
            | Q(product_name__icontains=search)
            | Q(brand__icontains=search)
            | Q(listing__product__title__icontains=search)
            | Q(listing__product__article__icontains=search)
            | Q(listing__product__brand__name__icontains=search)
        )
    if supplier:
        qs = qs.filter(listing__product__supplier=supplier)
    if city and city in CITY_BY_ID:
        city_ids = KaspiCompetitorOfferSnapshot.objects.filter(
            city_id=city, source__in=PUBLIC_SOURCES,
        ).values("listing_id")
        qs = qs.filter(listing_id__in=city_ids)
    elif city:
        city = ""

    active_ids = list(KaspiActiveExportRow.objects.filter(batch=batch)
                      .exclude(listing_id__isnull=True)
                      .values_list("listing_id", flat=True))
    states = competitor_states_for_listings(active_ids)
    valid_offers = {pk for pk, state in states.items() if state.competitor_count > 0}
    if status == "offers":
        qs = qs.filter(listing_id__in=valid_offers)
    elif status == "missing":
        qs = qs.exclude(listing_id__in=valid_offers)
    elif status == "unmatched":
        qs = qs.filter(listing__isnull=True)
    elif status == "draft":
        qs = qs.filter(listing__manual_price_draft__isnull=False)
    elif status == "higher":
        qualifying = [
            pk for pk, st in states.items() if st.is_fresh and st.best_price is not None
        ]
        qs = qs.filter(listing_id__in=qualifying, price__isnull=False)
        above = {pk for pk, price in qs.values_list("listing_id", "price")
                 if pk in states and states[pk].best_price is not None
                 and price > states[pk].best_price}
        qs = qs.filter(listing_id__in=above)

    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    rows = []
    for record in page.object_list:
        listing = record.listing
        state = _listing_state(listing, states) if listing else None
        draft = getattr(listing, "manual_price_draft", None) if listing else None
        # Output Apify for this exact ACTIVE row supersedes old cached direct-Kaspi data.
        apify_best = record.competitor_min_price
        best = apify_best if apify_best is not None else (state.best_price if state else None)
        rows.append({
            "active": record, "listing": listing,
            "item_title": record.product_name or (
                listing.product.title if listing else "Товар Kaspi без карточки ZPT.KZ"
            ),
            "article": (listing.product.article if listing else record.merchant_sku) or "—",
            "brand_name": record.brand or (
                listing.product.brand.name if listing and listing.product.brand else ""
            ) or "—",
            "our": record.price, "quantity": record.quantity,
            "draft": draft, "state": state,
            "count": state.competitor_count if state else 0,
            "state_label": (state.state_label if state else "Требует сопоставления"),
            "best": best,
            "apify_best": apify_best,
            "best_seller": record.competitor_seller,
            "best_city": record.competitor_city,
            "best_url": record.competitor_url,
            "apify_checked_at": record.competitor_checked_at,
            "stale": bool(state and state.is_stale) if apify_best is None else False,
            "delta": record.price - best if record.price is not None and best is not None else None,
        })
    total = KaspiActiveExportRow.objects.filter(batch=batch).count()
    matched = KaspiActiveExportRow.objects.filter(batch=batch, listing__isnull=False).count()
    apify_found = KaspiActiveExportRow.objects.filter(
        batch=batch, competitor_min_price__isnull=False
    ).count()
    found = KaspiActiveExportRow.objects.filter(
        batch=batch,
    ).filter(Q(competitor_min_price__isnull=False) | Q(listing_id__in=valid_offers)).count()
    drafted = KaspiActiveExportRow.objects.filter(
        batch=batch, listing__manual_price_draft__isnull=False
    ).count()
    start_page = max(1, min(page.number - 2, page.paginator.num_pages - 4))
    end_page = min(page.paginator.num_pages, start_page + 4)
    return {
        "monitored": rows, "page": page,
        "page_numbers": list(range(start_page, end_page + 1)),
        "summary": {
            "total": total, "found": found, "missing": total - found,
            "drafted": drafted, "unmatched": total - matched,
            "apify_found": apify_found,
        },
        "search": search, "supplier": supplier, "selected_city": city,
        "status": status, "active_batch": batch,
    }


@staff_member_required
def repricer_dashboard(request):
    listings_qs, search, supplier = _filtered_listings(request)
    # The KPI values represent all active listings, regardless of page/filter.
    total = ProductKaspiListing.objects.filter(is_active=True).count()
    all_ids = list(ProductKaspiListing.objects.filter(is_active=True).values_list("id", flat=True))
    all_states = competitor_states_for_listings(all_ids)
    found = sum(1 for st in all_states.values() if st.competitor_count > 0)
    drafted = KaspiManualPriceDraft.objects.filter(listing__is_active=True).count()
    city = (request.GET.get("city") or "").strip()
    if city and city in CITY_BY_ID:
        # City filter means a captured offer in that city exists, not that the
        # global minimum is necessarily from that city.
        city_ids = KaspiCompetitorOfferSnapshot.objects.filter(
            city_id=city, source__in=PUBLIC_SOURCES,
        ).values("listing_id")
        listings_qs = listings_qs.filter(pk__in=city_ids)
    elif city:
        city = ""
    status = (request.GET.get("status") or "").strip()
    if status == "offers":
        listings_qs = listings_qs.filter(pk__in=[i for i, s in all_states.items() if s.competitor_count])
    elif status == "missing":
        listings_qs = listings_qs.exclude(pk__in=[i for i, s in all_states.items() if s.competitor_count])
    elif status == "draft":
        listings_qs = listings_qs.filter(manual_price_draft__isnull=False)
    elif status == "higher":
        # Compare only available, demonstrably recent snapshots.
        price_pairs = {i: s.best_price for i, s in all_states.items() if s.is_fresh and s.best_price is not None}
        higher_ids = [
            i for i, own in ProductKaspiListing.objects.filter(
                pk__in=price_pairs, last_known_our_price__isnull=False
            ).values_list("id", "last_known_our_price") if own > price_pairs[i]
        ]
        listings_qs = listings_qs.filter(pk__in=higher_ids)

    pagesize = 20
    paginator = Paginator(listings_qs, pagesize)
    page = paginator.get_page(request.GET.get("page"))
    current = list(page.object_list.prefetch_related("manual_price_draft"))
    start_page = max(1, min(page.number - 2, paginator.num_pages - 4))
    end_page = min(paginator.num_pages, start_page + 4)
    page_numbers = list(range(start_page, end_page + 1))
    monitored = [
        _row(l, _listing_state(l, all_states), CITY_BY_ID.get(city, ""))
        for l in current
    ]

    public_snapshots = KaspiCompetitorOfferSnapshot.objects.filter(source__in=PUBLIC_SOURCES)
    latest_public_snapshot = public_snapshots.order_by("-captured_at").first()
    fresh_minutes = competitor_fresh_minutes()
    fresh_cutoff = timezone.now() - timedelta(minutes=fresh_minutes)
    city_rows = (
        KaspiCompetitorIngestBatch.objects.filter(source="office_collector")
        .exclude(city_id="").values("city_id")
        .annotate(last_collected=Max("captured_at"), articles=Count("listing_id", distinct=True))
    )
    city_stats = {r["city_id"]: r for r in city_rows}
    city_coverage = [
        {"name": name, "city_id": code,
         "articles": city_stats.get(code, {}).get("articles", 0),
         "last_collected": city_stats.get(code, {}).get("last_collected")}
        for code, name in KASPI_KZ_CITIES
    ]
    active_batch = _latest_active_batch()
    active_context = _active_assortment(request, active_batch) if active_batch else {}
    response = render(request, "repricer/dashboard.html", {
        "monitored": monitored, "page": page, "page_numbers": page_numbers, "search": search, "supplier": supplier,
        "status": status, "selected_city": city, "cities": KASPI_KZ_CITIES,
        "suppliers": ProductKaspiListing._meta.get_field("product").related_model.SUPPLIER_CHOICES,
        "summary": {"total": total, "found": found, "missing": total-found, "drafted": drafted},
        "last_collection": latest_public_snapshot,
        "latest_public_snapshot": latest_public_snapshot,
        "fresh_minutes": fresh_minutes,
        "fresh_snapshot_count": public_snapshots.filter(captured_at__gte=fresh_cutoff).count(),
        "public_snapshot_count": public_snapshots.count(),
        "city_coverage": city_coverage, "city_total": len(KASPI_KZ_CITIES),
        "covered_city_count": sum(bool(x["last_collected"]) for x in city_coverage),
        "cityless_legacy_count": KaspiCompetitorIngestBatch.objects.filter(
            source="office_collector", city_id=""
        ).count(),
        "collection_cities": KASPI_KZ_CITIES,
        "manual_run": KaspiManualCollectionRun.objects.filter(created_by=request.user).first(),
        "kaspi_upload_result": request.session.get("kaspi_upload_result"),
        "apify_result": request.session.get("repricer_apify_result"),
        **active_context,
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
def repricer_product(request, listing_id):
    listing = get_object_or_404(
        ProductKaspiListing.objects.select_related(
            "product", "product__brand", "product__category", "manual_price_draft"
        ), pk=listing_id, is_active=True,
    )
    state = _listing_state(listing, competitor_states_for_listings([listing.pk]))
    snapshots = KaspiCompetitorOfferSnapshot.objects.filter(
        listing=listing, source__in=PUBLIC_SOURCES
    )
    latest = snapshots.aggregate(latest=Max("captured_at"))["latest"]
    offers = list(snapshots.filter(captured_at=latest, is_available=True)
                  .order_by("price", "pk")) if latest else []
    own_ids, _ = configured_own_merchants()
    # Never label unknown own-merchant offers as competitors.
    offers = [o for o in offers if o.seller_code.strip().casefold() not in own_ids] if own_ids else []
    for offer in offers:
        offer.city_name = CITY_BY_ID.get(offer.city_id, "Не указан")
    response = render(request, "repricer/product.html", {
        "row": _row(listing, state), "offers": offers, "is_stale": state.is_stale,
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@staff_member_required
@require_POST
def repricer_save_price(request, listing_id):
    listing = get_object_or_404(ProductKaspiListing, pk=listing_id, is_active=True)
    raw = (request.POST.get("price") or "").strip()
    if not raw.isascii() or not raw.isdecimal() or not (1 <= len(raw) <= 10):
        messages.error(request, "Введите положительную цену целым числом в тенге.")
    else:
        price = int(raw)
        if not 1 <= price <= 999999999:
            messages.error(request, "Цена должна быть от 1 до 999 999 999 ₸.")
        else:
            KaspiManualPriceDraft.objects.update_or_create(
                listing=listing, defaults={"price": price, "updated_by": request.user}
            )
            messages.success(request, "Черновик цены сохранён. Цена на Kaspi не изменена.")
    return redirect("repricer:product", listing_id=listing.pk)


@staff_member_required
@require_POST
def repricer_save_prices(request):
    """Save only explicitly changed row prices; never alter the Kaspi listing."""
    saved = 0
    for key, raw in request.POST.items():
        if not key.startswith("price_"):
            continue
        try:
            listing_id = int(key[6:])
        except ValueError:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if not text.isascii() or not text.isdecimal() or len(text) > 10:
            messages.error(request, f"Цена товара {listing_id} не сохранена: неверное число.")
            continue
        value = int(text)
        if not (1 <= value <= 999999999):
            messages.error(request, f"Цена товара {listing_id} вне допустимого диапазона.")
            continue
        listing = ProductKaspiListing.objects.filter(pk=listing_id, is_active=True).first()
        if listing is None:
            continue
        draft = KaspiManualPriceDraft.objects.filter(listing_id=listing_id).first()
        original = draft.price if draft else listing.last_known_our_price
        if original == value:
            continue
        KaspiManualPriceDraft.objects.update_or_create(
            listing=listing, defaults={"price": value, "updated_by": request.user},
        )
        saved += 1
    if saved:
        messages.success(request, f"Сохранено черновиков цен: {saved}. На Kaspi цены не изменены.")
    else:
        messages.info(request, "Изменений цен для сохранения нет.")
    return redirect("repricer:dashboard")


@staff_member_required
def repricer_export(request):
    drafts = list(KaspiManualPriceDraft.objects.select_related("listing", "listing__product")
                  .filter(listing__is_active=True).order_by("listing__merchant_sku", "listing_id"))
    if not drafts:
        messages.warning(request, "Нет сохранённых изменений цен для выгрузки.")
        return redirect("repricer:dashboard")
    wb = Workbook()
    ws = wb.active
    ws.title = "Подготовленные цены"
    ws.append(["Merchant SKU", "Kaspi SKU", "Артикул", "Товар", "Текущая цена, ₸", "Новая цена, ₸"])
    for draft in drafts:
        listing = draft.listing
        ws.append([listing.merchant_sku, listing.master_sku, listing.product.article,
                   listing.product.title, listing.last_known_our_price, draft.price])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="142E4D")
    for col, width in {"A": 25, "B": 24, "C": 25, "D": 55, "E": 22, "F": 22}.items():
        ws.column_dimensions[col].width = width
    info = wb.create_sheet("Инструкция")
    info.append(["Черновик ручных изменений цен ZPT.KZ"])
    info.append(["Этот файл НЕ является подтверждённым шаблоном импорта Kaspi."])
    info.append(["Перед загрузкой перенесите SKU и цены в актуальный шаблон личного кабинета Kaspi."])
    info.append(["Экспорт не изменяет цены на Kaspi."])
    output = BytesIO()
    wb.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="zpt-kaspi-price-drafts.xlsx"'
    return response
