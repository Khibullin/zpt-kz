"""Single write path for warehouse balances. Never set quantity ad hoc."""

from __future__ import annotations

import uuid

from django.db import IntegrityError, transaction

from catalog.models import ProductWarehouseStock, StockMovement, Warehouse
from catalog.warehouses import KASPI_AVAILABLE_WAREHOUSE_CODE

NEGATIVE_TYPES = {
    StockMovement.MovementType.SALE,
    StockMovement.MovementType.TRANSFER_OUT,
}
POSITIVE_TYPES = {
    StockMovement.MovementType.OPENING,
    StockMovement.MovementType.RECEIPT,
    StockMovement.MovementType.TRANSFER_IN,
    StockMovement.MovementType.RETURN,
}


class StockServiceError(ValueError):
    """Invalid stock movement request."""


class InsufficientStockError(StockServiceError):
    """quantity_after would be negative."""


def resolve_warehouse(warehouse) -> Warehouse:
    if isinstance(warehouse, Warehouse):
        if not warehouse.is_active:
            raise StockServiceError(f'warehouse_inactive:{warehouse.code}')
        return warehouse
    code = str(warehouse or '').strip()
    if not code:
        raise StockServiceError('warehouse_required')
    try:
        found = Warehouse.objects.get(code=code)
    except Warehouse.DoesNotExist as exc:
        raise StockServiceError(f'warehouse_not_found:{code}') from exc
    if not found.is_active:
        raise StockServiceError(f'warehouse_inactive:{code}')
    return found


def get_stock_quantity(product, warehouse) -> int:
    """Current balance, or 0 if no row exists."""
    warehouse = resolve_warehouse(warehouse)
    stock = ProductWarehouseStock.objects.filter(
        product=product,
        warehouse=warehouse,
    ).first()
    if stock is None:
        return 0
    return int(stock.quantity)


def get_kaspi_available_stock(product) -> int:
    """Kaspi available stock = PP2 quantity only.

    Missing PP2 warehouse or missing balance returns 0 (exact warehouse
    layer, unlike the vitrine unknown-NULL of Product.stock_qty).
    Never adds PP1. Does not write Product.stock_qty and does not call
    the Kaspi API.
    """
    try:
        warehouse = Warehouse.objects.get(
            code=KASPI_AVAILABLE_WAREHOUSE_CODE,
            is_active=True,
        )
    except Warehouse.DoesNotExist:
        return 0
    stock = ProductWarehouseStock.objects.filter(
        product=product,
        warehouse=warehouse,
    ).first()
    if stock is None:
        return 0
    return int(stock.quantity)


def _lock_existing(product, warehouse: Warehouse):
    return (
        ProductWarehouseStock.objects.select_for_update()
        .filter(product=product, warehouse=warehouse)
        .first()
    )


def _get_or_create_locked(product, warehouse: Warehouse) -> ProductWarehouseStock:
    existing = _lock_existing(product, warehouse)
    if existing is not None:
        return existing
    try:
        return ProductWarehouseStock.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=0,
        )
    except IntegrityError:
        return _lock_existing(product, warehouse)


def _validate_delta(movement_type, quantity_delta: int):
    if movement_type == StockMovement.MovementType.OPENING and quantity_delta < 0:
        raise StockServiceError('opening_delta_must_be_non_negative')
    if movement_type in POSITIVE_TYPES and quantity_delta < 0:
        raise StockServiceError(f'{movement_type}_delta_must_be_non_negative')
    if movement_type in NEGATIVE_TYPES and quantity_delta > 0:
        raise StockServiceError(f'{movement_type}_delta_must_be_non_positive')
    if movement_type in NEGATIVE_TYPES and quantity_delta == 0:
        raise StockServiceError(f'{movement_type}_delta_must_be_negative')


def apply_stock_movement(
    *,
    product,
    warehouse,
    movement_type,
    quantity_delta,
    source,
    reference='',
    note='',
):
    """Apply one signed delta, persist balance + StockMovement, return movement."""
    if movement_type not in StockMovement.MovementType.values:
        raise StockServiceError(f'unknown_movement_type:{movement_type}')
    try:
        delta = int(quantity_delta)
    except (TypeError, ValueError) as exc:
        raise StockServiceError('quantity_delta_must_be_int') from exc
    _validate_delta(movement_type, delta)
    warehouse = resolve_warehouse(warehouse)
    source_text = str(source or '').strip()
    if not source_text:
        raise StockServiceError('source_required')

    with transaction.atomic():
        existing = _lock_existing(product, warehouse)
        before = int(existing.quantity) if existing is not None else 0
        if movement_type == StockMovement.MovementType.OPENING and existing is not None:
            raise StockServiceError('opening_requires_missing_balance')
        if delta == 0 and movement_type != StockMovement.MovementType.OPENING:
            return None
        after = before + delta
        if after < 0:
            raise InsufficientStockError(
                f'insufficient_stock:{warehouse.code}:{before}:{delta}'
            )
        if existing is None:
            stock = _get_or_create_locked(product, warehouse)
            before = int(stock.quantity)
            after = before + delta
            if after < 0:
                raise InsufficientStockError(
                    f'insufficient_stock:{warehouse.code}:{before}:{delta}'
                )
        else:
            stock = existing
        stock.quantity = after
        stock.save(update_fields=['quantity', 'updated_at'])
        return StockMovement.objects.create(
            product=product,
            warehouse=warehouse,
            movement_type=movement_type,
            quantity_delta=delta,
            quantity_before=before,
            quantity_after=after,
            source=source_text,
            reference=str(reference or ''),
            note=str(note or ''),
        )


def set_stock_quantity(
    *,
    product,
    warehouse,
    new_quantity,
    source,
    reference='',
    note='',
):
    """Set absolute quantity. First row is OPENING; later diffs are ADJUSTMENT.

    Missing balance: always write OPENING (including quantity 0).
    Existing balance with same quantity: no StockMovement.
    Existing balance with a different quantity: ADJUSTMENT.
    """
    try:
        target = int(new_quantity)
    except (TypeError, ValueError) as exc:
        raise StockServiceError('new_quantity_must_be_int') from exc
    if target < 0:
        raise StockServiceError('new_quantity_must_be_non_negative')
    warehouse = resolve_warehouse(warehouse)
    with transaction.atomic():
        existing = _lock_existing(product, warehouse)
        if existing is None:
            return apply_stock_movement(
                product=product,
                warehouse=warehouse,
                movement_type=StockMovement.MovementType.OPENING,
                quantity_delta=target,
                source=source,
                reference=reference,
                note=note,
            )
        before = int(existing.quantity)
        delta = target - before
        if delta == 0:
            return None
        return apply_stock_movement(
            product=product,
            warehouse=warehouse,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            quantity_delta=delta,
            source=source,
            reference=reference,
            note=note,
        )


def transfer_stock(
    *,
    product,
    from_warehouse,
    to_warehouse,
    quantity,
    source='transfer',
    reference='',
    note='',
):
    """Move quantity between warehouses in one transaction. Rolls back on shortfall."""
    try:
        qty = int(quantity)
    except (TypeError, ValueError) as exc:
        raise StockServiceError('quantity_must_be_int') from exc
    if qty <= 0:
        raise StockServiceError('transfer_quantity_must_be_positive')
    from_wh = resolve_warehouse(from_warehouse)
    to_wh = resolve_warehouse(to_warehouse)
    if from_wh.pk == to_wh.pk:
        raise StockServiceError('transfer_same_warehouse')
    ref = str(reference or '').strip() or uuid.uuid4().hex
    with transaction.atomic():
        for warehouse in sorted((from_wh, to_wh), key=lambda item: item.pk):
            _lock_existing(product, warehouse)
        apply_stock_movement(
            product=product,
            warehouse=from_wh,
            movement_type=StockMovement.MovementType.TRANSFER_OUT,
            quantity_delta=-qty,
            source=source,
            reference=ref,
            note=note,
        )
        apply_stock_movement(
            product=product,
            warehouse=to_wh,
            movement_type=StockMovement.MovementType.TRANSFER_IN,
            quantity_delta=qty,
            source=source,
            reference=ref,
            note=note,
        )
        return ref
