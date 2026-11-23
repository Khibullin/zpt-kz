"""Manual Kaspi active-listing export upload for staff.

Only observed Kaspi listing prices/quantities are refreshed. Product, PP1/PP2
stock, SKU mapping, price drafts and Kaspi itself are never modified.
"""
from pathlib import Path
from zipfile import BadZipFile
from tempfile import TemporaryDirectory

from django.contrib import messages
from django.db import transaction
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from catalog.ag_parts_import import cell_text, normalize_header
from catalog.kaspi_listing_facts import (
    load_kaspi_fact_rows, summarize_kaspi_fact_rows, sync_kaspi_listing_facts,
)
from catalog.models import KaspiListingFactSnapshot, SellerProfile
from .models import KaspiActiveExportRow
from catalog.warehouse_stock_sync import parse_stock_quantity

def _optional_index(headers, names):
    normalized = [normalize_header(x) for x in headers]
    wanted = {normalize_header(x) for x in names}
    return next((i for i, name in enumerate(normalized) if name in wanted), None)


def _optional_text(values, index, limit):
    if index is None or index >= len(values):
        return ""
    return cell_text(values[index])[:limit]


def _persist_active_rows(result, source_rows):
    """Preserve every file row, including unresolved/invalid, without inventing matches."""
    from catalog.models import CatalogImportBatch
    batch = CatalogImportBatch.objects.get(pk=result.batch_id)
    headers = result.headers
    indices = {
        "name": _optional_index(headers, ("Название", "Наименование", "Наименование товара", "Название товара", "name", "title", "product name", "model")),
        "brand": _optional_index(headers, ("Бренд", "Марка", "brand")),
        "merchant": _optional_index(headers, ("Merchant SKU", "merchant_sku", "Артикул продавца", "Артикул", "Код товара продавца")),
    }
    original = {number: values for number, values in source_rows}
    records = []
    for row in result.rows:
        values = original.get(row.row_number, [])
        # Preserve usable prices/quantities even for unmatched or duplicate
        # source SKUs; do not confuse missing or invalid values with zero.
        raw_price = values[result.column_map["price"]] if len(values) > result.column_map["price"] else None
        raw_qty = values[result.column_map["quantity"]] if len(values) > result.column_map["quantity"] else None
        observed_price, price_error = parse_stock_quantity(raw_price)
        observed_qty, qty_error = parse_stock_quantity(raw_qty)
        records.append(KaspiActiveExportRow(
            batch=batch, row_number=row.row_number,
            master_sku=row.master_sku[:128],
            product_name=_optional_text(values, indices["name"], 500),
            brand=_optional_text(values, indices["brand"], 150),
            merchant_sku=row.merchant_sku or _optional_text(values, indices["merchant"], 128),
            price=observed_price if not price_error else None,
            quantity=observed_qty if not qty_error else None,
            listing_id=row.listing_id, status=row.status, reason=row.reason[:255],
        ))
    if not records:
        raise ValueError("В выгрузке нет строк товаров.")
    KaspiActiveExportRow.objects.bulk_create(records, batch_size=500)
    # A fully uploaded ACTIVE file defines the assortment, even when some SKU
    # need reconciliation. No catalog products or Kaspi publication are created.
    batch.source_scope = CatalogImportBatch.SCOPE_FULL
    batch.save(update_fields=["source_scope"])


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
            # Preserve the original source filename in the import audit batch.
            path = Path(tmp) / Path(upload.name).name
            with path.open("wb") as destination:
                for chunk in upload.chunks():
                    destination.write(chunk)
            _sheet, _headers, _mapping, source_rows = load_kaspi_fact_rows(path)
            if not source_rows:
                raise ValueError("В файле нет товаров.")
            with transaction.atomic():
                result = sync_kaspi_listing_facts(
                    path=path,
                    seller=matches[0],
                    apply=True,
                    source=KaspiListingFactSnapshot.SOURCE_ACTIVE_XLSX,
                )
                _persist_active_rows(result, source_rows)
    except (ValueError, OSError, KeyError, RuntimeError, BadZipFile) as exc:
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
        "active_source": True,
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
        f"не сопоставлено/ошибок {problems}. Все строки ACTIVE сохранены для репрайсинга. Складские остатки не изменены."
    )
    return redirect("repricer:dashboard")
