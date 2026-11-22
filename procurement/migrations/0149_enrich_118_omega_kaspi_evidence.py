"""Fill in source evidence for 118 Omega/Kaspi recheck cards without confirming identity."""
import base64, json, zlib
from django.db import migrations

DATA="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"
SOURCE="Omega_6exports_2026-11-21_repeat-check"
def apply(apps,schema_editor):
    Candidate=apps.get_model("procurement","KaspiBuyingCandidate")
    Audit=apps.get_model("procurement","KaspiPriceAudit")
    Material=apps.get_model("procurement","BuyingComparisonMaterial")
    data=json.loads(zlib.decompress(base64.b64decode(DATA)).decode())
    if len(data)!=118: raise RuntimeError("Incorrect source evidence count")
    changed=0
    for pid,title,image,seller in data:
        audit=Audit.objects.filter(source_filename=SOURCE,product_id=pid).first()
        if audit is None: continue
        audit.card_title=title[:500]
        audit.seller_name=seller[:180]
        audit.save(update_fields=["card_title","seller_name"])
        material=Material.objects.filter(offer_id=audit.offer_id,
                 source_filename=SOURCE,kaspi_product_id=pid).first()
        if material:
            old=material.kaspi_data if isinstance(material.kaspi_data,dict) else {}
            old.update({"id":pid,"name":title,"title":title,
                "images":[image] if image.startswith("https://") else [],
                "sellerName":seller,"sellerPrice":str(audit.min_price),
                "source_note":"Minimum observed in a seller offer of the 21.11.2026 Apify export; not a live price"})
            material.kaspi_data=old
            material.save(update_fields=["kaspi_data","checked_at"])
        candidate=Candidate.objects.filter(product_id=pid).first()
        if candidate and candidate.match_status!="verified" and candidate.offer_id==audit.offer_id:
            candidate.title=title[:500]
            candidate.minimum_price=audit.min_price
            candidate.city_offers=[{"city":"Almaty","sellers":[{
                "sellerName":seller,"price":str(audit.min_price)}]}]
            candidate.save(update_fields=["title","minimum_price","city_offers","imported_at"])
        changed+=1
    if changed!=118: raise RuntimeError(f"Expected 118 audit source identities; got {changed}")

class Migration(migrations.Migration):
    atomic=True
    dependencies=[("procurement","0148_stage_118_omega_recheck")]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
