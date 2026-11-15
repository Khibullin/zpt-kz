"""One exact Parts Mall photo and fitment; never changes trade or Kaspi data."""
import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

MANIFEST_SHA256 = "81dac51d8c2fe168472855f8fbada3e8e6a450ff718ca76811b953f743c0a190"
BATCH = "weekend-20261114-k"
TASK_STATUS = "weekend14k_ready"

@lru_cache(maxsize=1)
def reviewed_rows():
    raw=(Path(__file__).parent/"data"/"reviewed_weekend_20261114_k.json").read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA256: raise ValueError("Reviewed Parts Mall manifest changed")
    payload=json.loads(raw);rows=payload["sources"]
    if payload["checked_on"]!="2026-11-14" or len(rows)!=1: raise ValueError("Unexpected Parts Mall batch")
    return rows

def exact_offer(offer,row):
    from procurement.analysis import stock_status
    return bool(offer and offer.pk==row["offer_id"] and offer.supplier_id==row["supplier_id"]==5 and offer.brand==row["brand"] and offer.article==row["article"] and stock_status(offer.stock)=="in_stock" and str(offer.stock)==row["stock"] and offer.cost==Decimal(row["cost"]))

def stage_review(apps,schema_editor):
    if schema_editor.connection.alias!="default": return
    from django.db import transaction
    Offer=apps.get_model("procurement","Offer");Fitment=apps.get_model("procurement","VehicleFitment");Listing=apps.get_model("procurement","RetailListing");Activity=apps.get_model("procurement","Activity");Supplier=apps.get_model("procurement","Supplier");Task=apps.get_model("catalog","SupplierPhotoTask")
    if not Supplier.objects.filter(pk=5,name="ZakazAuto — Костанай").exists(): return
    counts={"photo_tasks":0,"fitment_offers":0,"make_model_pairs":0,"configuration_rows":0}
    with transaction.atomic():
        for row in reviewed_rows():
            offer=Offer.objects.select_for_update().filter(pk=row["offer_id"]).first()
            listing=Listing.objects.select_related("product").filter(offer_id=row["offer_id"],product_id=row["product_id"],enabled=True).first()
            if not exact_offer(offer,row) or listing is None: continue
            product=listing.product
            if product.status!="active" or product.article!=row["article"]: continue
            if not Fitment.objects.filter(offer_id=offer.pk).exists():
                for fit in row["fitments"]: Fitment.objects.create(offer_id=offer.pk,make=fit["make"],model=fit["model"],details=fit["details"],configurations=fit["configurations"],source_url=row["source_url"],verified=True)
                metadata=dict(offer.characteristics or {});metadata["reviewed_applicability"]={"source_label":row["source_label"],"source_kind":row["source_kind"],"source_urls":row["source_urls"],"checked_on":"2026-11-14","source_article":row["manufacturer_article"],"source_brand":"PMC / Parts-Mall","status":"sourced","note":row["fitment_note"]};offer.characteristics=metadata;offer.save(update_fields=["characteristics"])
                counts["fitment_offers"]+=1;counts["make_model_pairs"]+=len(row["fitments"]);counts["configuration_rows"]+=sum(len(fit["configurations"]) for fit in row["fitments"])
            if not product.main_image:
                task=Task.objects.select_for_update().select_related("offer").filter(pk=row["task_id"],product_id=product.pk,offer_id=offer.pk).first()
                if task and task.offer.brand==row["brand"] and task.offer.article==row["article"]:
                    task.source_url=row["source_url"];task.status=TASK_STATUS;task.note="Точная карточка и фото производителя проверены; ожидает единственного потребителя.";task.evidence={**(task.evidence or {}),"reviewed_batch":BATCH,"reviewed_manifest_sha256":MANIFEST_SHA256,"source_kind":row["source_kind"],"source_label":row["source_label"],"source_url":row["source_url"],"verified_image_url":row["verified_image_url"],"verified_image_sha256":row["verified_image_sha256"],"verified_image_size":row["verified_image_size"],"checked_on":"2026-11-14"};task.save(update_fields=["source_url","status","note","evidence","updated_at"]);counts["photo_tasks"]+=1
        Activity.objects.create(user_id=None,action="reviewed_weekend_parts_mall_import",details={"batch":BATCH,**counts})
