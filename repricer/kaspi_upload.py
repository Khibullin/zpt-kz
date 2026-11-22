"""Manual Kaspi active-listing export upload for staff.

Only observed Kaspi listing prices/quantities are refreshed. Product, PP1/PP2
stock, SKU mapping, price drafts and Kaspi itself are never modified.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from catalog.kaspi_listing_facts import summarize_kaspi_fact_rows, sync_kaspi_listing_facts
from catalog.models import KaspiListingFactSnapshot, SellerProfile

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_SUFFIXES = {".xlsx", ".csv"}


@staff_member_required
@require_POST
def upload_kaspi_assortment(request):
    upload = request.FILES.get("kaspi_file")
    if not upload:
        messages.error(request, "Выберите файл выгрузки Kaspi (.xlsx или .csv).")
        return redirect("repricer:dashboard")
    suffix = Path(upload.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        messages.error(request, "Допустимы только файлы Kaspi в формате XLSX или CSV.")
        return redirect("repricer:dashboard")
    if upload.size > MAX_UPLOAD_BYTES:
        messages.error(request, "Файл больше 15 МБ. Разделите выгрузку на части.")
        return redirect("repricer:dashboard")

    matches = list(SellerProfile.objects.filter(name__iexact="AG Parts")[:2])
    if len(matches) != 1:
        messages.error(request, "Невозможно однозначно определить продавца AG Parts. Импорт отменён.")
        return redirect("repricer:dashboard")

    try:
        with TemporaryDirectory(prefix="zpt_kaspi_import_") as tmp:
            path = Path(tmp) / ("kaspi_upload" + suffix)
            with path.open("wb") as destination:
                for chunk in upload.chunks():
                    destination.write(chunk)
            result = sync_kaspi_listing_facts(
                path=path,
                seller=matches[0],
                apply=True,
                source=KaspiListingFactSnapshot.SOURCE_MANUAL_IMPORT,
            )
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        messages.error(request, "Выгрузку не удалось прочитать: " + str(exc)[:250])
        return redirect("repricer:dashboard")

    counts = summarize_kaspi_fact_rows(result.rows)
    matched = counts["updated"] + counts["matched_no_change"]
    problems = counts["total_rows"] - matched
    # Per-user session shows the latest upload result; don't store full uploads in cookies.
    request.session["kaspi_upload_result"] = {
        "filename": Path(upload.name).name[:120],
        "rows": counts["total_rows"],
        "matched": matched,
        "updated": counts["updated"],
        "missing": counts["missing_listing"],
        "ambiguous": counts["ambiguous_listing"],
        "invalid": counts["invalid_price"] + counts["invalid_quantity"],
        "duplicates": counts["duplicate_master_sku"],
        "skipped": problems,
        "unmapped_examples": [
            {"sku": row.master_sku[:90], "status": row.status}
            for row in result.rows
            if row.status in ("MISSING_LISTING", "AMBIGUOUS_LISTING", "DUPLICATE_MASTER_SKU")
        ][:20],
    }
    messages.success(
        request,
        f"Файл Kaspi обработан: строк {counts['total_rows']}, "
        f"найдено наших товаров {matched}, обновлено цен/остатков {counts['updated']}, "
        f"не сопоставлено/ошибок {problems}. Складские остатки не изменены."
    )
    return redirect("repricer:dashboard")
