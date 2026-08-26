"""Upsert ProductBarcode from a WMS-style workbook. Never deletes missing codes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from catalog.ag_parts_import import (
    extract_article,
    inspect_workbook,
    load_sheet_rows,
    normalize_article,
    normalize_header,
)
from catalog.models import Product, ProductBarcode, SellerProfile

BARCODE_HEADERS = (
    'штрих коды', 'штрихкоды', 'штрихкод', 'barcode', 'barcodes',
    'ean', 'ean13', 'gtin',
)


@dataclass
class BarcodeSyncResult:
    article: str
    action: str
    codes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    product_id: int | None = None


def split_barcode_cell(raw) -> list[str]:
    text = '' if raw is None else str(raw)
    parts = re.split(r'[\n\r,;|]+', text)
    codes = []
    seen = set()
    for part in parts:
        code = re.sub(r'\s+', '', part)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def detect_barcode_column(headers) -> int | None:
    for index, header in enumerate(headers):
        if normalize_header(header) in BARCODE_HEADERS:
            return index
    return None


def sync_barcodes_from_xlsx(
    *,
    xlsx_path,
    seller: SellerProfile,
    dry_run: bool,
    articles=None,
):
    path = Path(xlsx_path)
    inspection = inspect_workbook(path)
    headers, column_map, data_rows, _images = load_sheet_rows(
        path,
        inspection.chosen_sheet,
    )
    if 'article' not in column_map:
        raise ValueError(f'Не найдена колонка артикула. Заголовки: {headers}')
    barcode_idx = detect_barcode_column(headers)
    if barcode_idx is None:
        raise ValueError(f'Не найдена колонка штрихкодов. Заголовки: {headers}')

    wanted = None
    if articles:
        wanted = {normalize_article(item) for item in articles if item}

    results = []
    for row_number, values in data_rows:
        raw_article = values[column_map['article']] if column_map['article'] < len(values) else ''
        article, article_key = extract_article(raw_article)
        if wanted and article_key not in wanted:
            continue
        codes = split_barcode_cell(
            values[barcode_idx] if barcode_idx < len(values) else ''
        )
        result = BarcodeSyncResult(article=article or article_key, action='skipped', codes=codes)
        if not article:
            result.action = 'error'
            result.errors.append('empty_article')
            results.append(result)
            continue
        product = Product.objects.filter(
            seller_profile=seller,
            article=article,
        ).first()
        if product is None:
            result.action = 'skipped'
            result.warnings.append('product_not_found')
            results.append(result)
            continue
        result.product_id = product.pk
        if not codes:
            result.action = 'unchanged'
            result.warnings.append('no_barcodes_in_row')
            results.append(result)
            continue
        created = 0
        if dry_run:
            existing = set(
                ProductBarcode.objects.filter(product=product).values_list('code', flat=True)
            )
            created = sum(1 for code in codes if code not in existing)
            result.action = 'created' if created else 'unchanged'
            results.append(result)
            continue
        has_primary = ProductBarcode.objects.filter(product=product, is_primary=True).exists()
        for code in codes:
            _obj, was_created = ProductBarcode.objects.get_or_create(
                product=product,
                code=code,
                defaults={
                    'source': ProductBarcode.SOURCE_WMS,
                    'is_primary': not has_primary,
                },
            )
            if was_created:
                created += 1
                has_primary = True
        result.action = 'created' if created else 'unchanged'
        results.append(result)
    return results, inspection
