"""Warehouse codes used by the stock layer.

Lookup warehouses by code, never by display name.
Kaspi available stock is the PP2 balance only — never PP1+PP2.
"""

WAREHOUSE_CODE_PP1 = 'PP1'
WAREHOUSE_CODE_PP2 = 'PP2'

KASPI_AVAILABLE_WAREHOUSE_CODE = WAREHOUSE_CODE_PP2

DEFAULT_WAREHOUSES = (
    (WAREHOUSE_CODE_PP1, 'Основной склад'),
    (WAREHOUSE_CODE_PP2, 'Fulfillment'),
)
