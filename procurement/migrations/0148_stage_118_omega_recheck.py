"""Stage the 118 reviewed Omega/Kaspi snapshots from six Apify exports, 21 Nov 2026.

A source snapshot is not proof of physical product identity or current stock.
No existing validated decision or permanent buying snapshot is overwritten.
"""
import base64, json, zlib, re
from decimal import Decimal
from django.db import migrations

DATA = "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"
SOURCE = "Omega_6exports_2026-11-21_repeat-check"
def norm(s):
    return re.sub(r"[^A-Z0-9А-Я]", "", str(s or "").upper())

def apply(apps, schema_editor):
    Offer=apps.get_model("procurement","Offer")
    Candidate=apps.get_model("procurement","KaspiBuyingCandidate")
    Audit=apps.get_model("procurement","KaspiPriceAudit")
    Buying=apps.get_model("procurement","PermanentBuyingItem")
    Material=apps.get_model("procurement","BuyingComparisonMaterial")
    Task=apps.get_model("procurement","KaspiReviewTask")
    data=json.loads(zlib.decompress(base64.b64decode(DATA)).decode())
    if len(data)!=118: raise RuntimeError("Source is not the approved 118-row snapshot")
    rows={}
    for o in Offer.objects.filter(supplier__name="Omega").iterator(chunk_size=1000):
        rows.setdefault((norm(o.brand),norm(o.article)),[]).append(o)
    eligible_count=0
    observed_count=0
    for brand,article,pid,observed,slug in data:
        matches=rows.get((norm(brand),norm(article)),[])
        matches=[o for o in matches if str(o.stock or "").strip() not in ("","0","Нет","нет")]
        matches.sort(key=lambda o:(o.cost or Decimal("999999999"),o.pk))
        if not matches: continue
        offer=matches[0]
        if offer.cost is None or offer.cost<=0: continue
        price=Decimal(str(observed))
        cost=offer.cost
        selling=(price*Decimal(".97")).quantize(Decimal(".01"))
        profit=(selling*Decimal(".875")-cost-Decimal("250")).quantize(Decimal(".01"))
        roi=(profit*Decimal("100")/cost).quantize(Decimal(".0001"))
        url="https://kaspi.kz/shop/p/"+slug+"/"
        # Preserve a verified association; never rewrite user decisions.
        prior=Candidate.objects.filter(product_id=pid).first()
        if not prior:
            Candidate.objects.create(product_id=pid,title=(brand+" "+article)[:500],
                brand=brand[:120],url=url,minimum_price=price,city_offers=[],
                offer=offer,match_status="candidate",source_filename=SOURCE)
        elif prior.match_status!="verified" and (prior.offer_id is None or prior.offer_id==offer.pk):
            prior.offer=offer
            prior.source_filename=SOURCE
            prior.save(update_fields=["offer","source_filename","imported_at"])
        Audit.objects.get_or_create(offer=offer,source_filename=SOURCE,defaults={
            "min_price":price,"product_id":pid,"card_url":url,
            "card_title":brand+" "+article,"seller_name":"",
            "cost_snapshot":cost,"commission_snapshot":Decimal("12.5"),
            "expenses_snapshot":Decimal("250"),"profit":profit,"roi":roi,
            "card_ids":[pid],"identity_status":"needs_review"})
        Material.objects.get_or_create(offer=offer,source_filename=SOURCE,
            kaspi_product_id=pid,defaults={
                "supplier_url":offer.supplier_url or "","kaspi_url":url,
                "supplier_data":{"brand":offer.brand,"article":offer.article,
                    "name":offer.name,"category":offer.category,"cost":str(cost),
                    "stock":offer.stock,"description":offer.description or "",
                    "applicability":offer.applicability or "",
                    "images":[offer.photo_url] if offer.photo_url else [],
                    "source_note":"Omega price-list snapshot; current fulfillment not verified"},
                "kaspi_data":{"id":pid,"brand":brand,"article":article,
                    "name":brand+" "+article,"minPrice":str(price),
                    "source_note":"Kaspi Apify 21.11.2026 seller minimum; source is historical"},
                "supplier_status":"local_snapshot","kaspi_status":"local_snapshot",
                "supplier_error":"Confirm current stock and full product identity",
                "kaspi_error":"Review images, specs, fitment; screenshot is separate evidence"})
        observed_count+=1
        if roi>=Decimal("50"):
            eligible_count+=1
            Buying.objects.get_or_create(offer=offer,defaults={
                "source":"apify","product_id":pid,
                "title":brand+" "+article,"kaspi_url":url,
                "cost_snapshot":cost,"kaspi_price_snapshot":price,
                "commission_snapshot":Decimal("12.5"),
                "expenses_snapshot":Decimal("250"),"profit_snapshot":profit,
                "roi_snapshot":roi,"identity_status":"candidate",
                "photo_url":""})
        Task.objects.get_or_create(offer=offer,defaults={
            "reason":"Повторная проверка 6 выгрузок Kaspi 21.11.2026: требуется подтверждение",
            "priority":2,"status":"pending"})
    if observed_count<100:
        raise RuntimeError(f"Unexpectedly few Omega exact matches: {observed_count}")

class Migration(migrations.Migration):
    atomic=True
    dependencies=[("procurement","0147_remove_non_omega_supplier_price_lists")]
    operations=[migrations.RunPython(apply,migrations.RunPython.noop)]
