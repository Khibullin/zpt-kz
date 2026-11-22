"""One-time removal of obsolete non-Omega supplier price-list rows.

User-approved 2026-11-22. Do not reload/replace Omega rows.
Preserve actual purchase orders even when their supplier is not Omega.
This is intentionally irreversible: previous supplier price-list data is deleted.
"""
from django.db import migrations

OFFER_CHILD_TABLES = (
    "catalog_supplierphototask",
    "procurement_buyingcomparisonmaterial",
    "procurement_draft",
    "procurement_favorite",
    "procurement_kaspicardsearch",
    "procurement_kaspipreparedcard",
    "procurement_kaspipriceaudit",
    "procurement_kaspireviewtask",
    "procurement_kaspisellersnapshot",
    "procurement_permanentbuyingitem",
    "procurement_profitableofferrecord",
    "procurement_retaillisting",
    "procurement_selection",
    "procurement_vehiclefitment",
)

def remove_non_omega_price_lists(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM procurement_supplier WHERE name = %s", ["Omega"])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Omega supplier missing; refusing to remove any price lists")
        omega_id = row[0]
        cursor.execute("SELECT count(*) FROM procurement_offer WHERE supplier_id = %s", [omega_id])
        omega_before = cursor.fetchone()[0]
        if omega_before < 19000:
            raise RuntimeError(
                f"Expected the existing Omega price list; found only {omega_before}. "
                "Refusing destructive cleanup."
            )
        cursor.execute("SELECT count(*) FROM procurement_offer WHERE supplier_id <> %s", [omega_id])
        obsolete_count = cursor.fetchone()[0]
        if not obsolete_count:
            return
        tables = set(connection.introspection.table_names(cursor))
        # Keep independent Apify observations, but detach obsolete supplier matches.
        if "procurement_kaspibuyingcandidate" in tables:
            cursor.execute(
                "UPDATE procurement_kaspibuyingcandidate "
                "SET offer_id = NULL, match_status = 'unmatched' "
                "WHERE offer_id IN "
                "(SELECT id FROM procurement_offer WHERE supplier_id <> %s)",
                [omega_id],
            )
        # Dependent price audits, public listings and tasks cannot survive the
        # removal of their non-Omega source offer. Omega-linked rows are untouched.
        for table in OFFER_CHILD_TABLES:
            if table not in tables:
                continue
            cursor.execute(
                f'DELETE FROM "{table}" WHERE offer_id IN '
                "(SELECT id FROM procurement_offer WHERE supplier_id <> %s)",
                [omega_id],
            )
        cursor.execute(
            "DELETE FROM procurement_offer WHERE supplier_id <> %s", [omega_id]
        )
        # Historical price update records are not price-list data for Omega.
        # Preserve all purchase orders including the one tied to a China supplier.
        if "procurement_priceupdate" in tables:
            if "procurement_profitableofferrecord" in tables:
                cursor.execute(
                    "UPDATE procurement_profitableofferrecord SET first_batch_id = NULL "
                    "WHERE first_batch_id IN "
                    "(SELECT id FROM procurement_priceupdate WHERE supplier_id <> %s)",
                    [omega_id],
                )
            if "procurement_kaspireviewtask" in tables:
                cursor.execute(
                    "UPDATE procurement_kaspireviewtask SET batch_id = NULL "
                    "WHERE batch_id IN "
                    "(SELECT id FROM procurement_priceupdate WHERE supplier_id <> %s)",
                    [omega_id],
                )
            cursor.execute(
                "DELETE FROM procurement_priceupdate WHERE supplier_id <> %s",
                [omega_id],
            )
        if "procurement_wholesaleselectionrule" in tables:
            cursor.execute(
                "UPDATE procurement_wholesaleselectionrule SET supplier_id = NULL "
                "WHERE supplier_id <> %s", [omega_id]
            )
        if "inventory_supplieritem" in tables:
            cursor.execute(
                "DELETE FROM inventory_supplieritem WHERE supplier_id <> %s",
                [omega_id],
            )
        # Retain supplier metadata solely if needed by a real purchase order.
        cursor.execute(
            "DELETE FROM procurement_supplier AS s WHERE s.id <> %s "
            "AND NOT EXISTS "
            "(SELECT 1 FROM procurement_purchaseorder po WHERE po.supplier_id = s.id)",
            [omega_id],
        )
        cursor.execute("SELECT count(*) FROM procurement_offer WHERE supplier_id = %s", [omega_id])
        omega_after = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM procurement_offer WHERE supplier_id <> %s", [omega_id])
        leftovers = cursor.fetchone()[0]
        if omega_after != omega_before or leftovers:
            raise RuntimeError(
                f"Cleanup validation failed: Omega before={omega_before}, "
                f"after={omega_after}, other supplier offers={leftovers}"
            )

class Migration(migrations.Migration):
    atomic = True
    dependencies = [
        ("procurement", "0146_crossquery_recalculated_1411"),
    ]
    operations = [
        migrations.RunPython(remove_non_omega_price_lists, migrations.RunPython.noop),
    ]
