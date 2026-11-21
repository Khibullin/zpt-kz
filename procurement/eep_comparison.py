"""Read-only supplier/Kaspi comparison for profitable buying offers.

Use the same Apify price-audit card selected by the visible buying list.
Never infer missing product attributes from a price-only export.
"""
from urllib.parse import urlsplit
import re
from .comparison_first_ten import FIRST_TEN_OFFER_IDS

from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from .models import Offer, PermanentBuyingItem, KaspiBuyingCandidate, KaspiPriceAudit, BuyingComparisonMaterial
from .views import access, context


def _string(value):
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(filter(None, (_string(x) for x in value)))
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_string(v)}" for key, v in value.items() if _string(v))
    return str(value).strip()


def _first(source, *names):
    if not isinstance(source, dict):
        return ""
    for name in names:
        value = _string(source.get(name))
        if value:
            return value
    return ""


def _image_urls(values):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    return [url for url in values if isinstance(url, str) and
            url.startswith(("https://", "http://"))][:8]


FIELDS = (
    ("name", "Наименование товара"),
    ("brand", "Производитель / бренд"),
    ("article", "Артикул"),
    ("category", "Категория"),
    ("description", "Описание"),
    ("specs", "Технические характеристики"),
    ("fitment", "Применимость к автомобилям"),
    ("package", "Комплектность"),
    ("oem", "Оригинальные номера (OEM)"),
    ("analogs", "Аналоги и заменители"),
)


@access()
def comparison(request, pk):
    offer = get_object_or_404(Offer.objects.select_related("supplier"),
                              pk=pk, permanent_buying_item__isnull=False)
    record = get_object_or_404(PermanentBuyingItem, offer=offer)
    source_file = request.GET.get("apify_source", "").strip()[:255]
    audits = KaspiPriceAudit.objects.filter(offer=offer)
    if source_file:
        audits = audits.filter(source_filename=source_file)
    audit = audits.order_by("-observed_at", "-pk").first()
    # If a specific export was requested, do NOT borrow a card from another export.
    product_id = (audit.product_id if audit else "" if source_file else record.product_id)
    candidate = (KaspiBuyingCandidate.objects.filter(product_id=product_id).first()
                 if product_id else None)
    source = offer.characteristics if isinstance(offer.characteristics, dict) else {}
    # These are source-labelled observation objects, not cross-shop merged fields.
    firsthand = source.get("firsthand_omega_20261117") or {}
    if not isinstance(firsthand, dict) or str(firsthand.get("kaspi_product_id", "")) != product_id:
        firsthand = {}
    kaspi_source = source.get("kaspi_card_details") or source.get("kaspi_details") or {}
    if not isinstance(kaspi_source, dict) or str(kaspi_source.get("product_id", "")) != product_id:
        kaspi_source = {}
    fits = offer.fitments.filter(verified=True).order_by("make", "model")[:200]
    fitment = "; ".join(
        " ".join(filter(None, [f.make, f.model, f.details])) for f in fits
    )
    supplier_data = {
        "name": offer.name, "brand": offer.brand, "article": offer.article,
        "category": offer.category, "description": offer.description,
        "specs": _first(source, "technical_characteristics", "specifications", "technical_specs", "specs"),
        "fitment": fitment or offer.applicability,
        "package": _first(source, "package", "packaging", "completeness", "kit_contents"),
        "oem": _first(source, "oem", "oem_numbers", "oe_numbers", "original_numbers"),
        "analogs": _first(source, "analogues", "analogs", "cross_references", "replacements"),
    }
    title = ((audit.card_title if audit else "") or
             (candidate.title if candidate else "") or
             _first(kaspi_source, "title") or _first(firsthand, "kaspi_title"))
    # Only expose identity visibly present as whole tokens in the exact selected
    # Kaspi card title; this is a preliminary transcription, not verification.
    title_tokens = set(re.findall(r'[^\\W_]+(?:[-/.][^\\W_]+)*', title.casefold(), re.UNICODE))
    title_brand = offer.brand if offer.brand.casefold() in title_tokens else ''
    title_article = offer.article if offer.article.casefold() in title_tokens else ''
    kaspi_data = {
        "name": title,
        "brand": _first(kaspi_source, "brand") or (candidate.brand if candidate else "") or title_brand,
        "article": _first(kaspi_source, "article") or title_article,
        "category": _first(kaspi_source, "category"),
        "description": _first(kaspi_source, "description"),
        "specs": _first(kaspi_source, "technical_characteristics", "specifications", "specs", "characteristics"),
        "fitment": _first(kaspi_source, "fitment", "applicability"),
        "package": _first(kaspi_source, "package", "completeness"),
        "oem": _first(kaspi_source, "oem", "oem_numbers", "original_numbers"),
        "analogs": _first(kaspi_source, "analogs", "replacements"),
    }
    # Enrichment is stored separately and scoped to the chosen Apify export/card.
    material = (BuyingComparisonMaterial.objects.filter(
        offer=offer, source_filename=source_file or (audit.source_filename if audit else ''),
        kaspi_product_id=product_id).first())
    if material:
        for key in supplier_data:
            value = material.supplier_data.get(key) if isinstance(material.supplier_data, dict) else None
            if value:
                supplier_data[key] = value
        if isinstance(material.kaspi_data, dict):
            for key in kaspi_data:
                value = material.kaspi_data.get(key)
                if value:
                    kaspi_data[key] = value
    rows = []
    for key, label in FIELDS:
        supplier_value, kaspi_value = _string(supplier_data[key]), _string(kaspi_data[key])
        rows.append({"label": label, "supplier": supplier_value or "Нет данных",
                     "kaspi": kaspi_value or "Нет данных",
                     "different": bool(supplier_value and kaspi_value and
                                       supplier_value.casefold() != kaspi_value.casefold())})
    supplier_photos = _image_urls([offer.photo_url] if offer.photo_url else [])
    supplier_photos.extend(url for url in _image_urls(source.get("supplier_images", []))
                           if url not in supplier_photos)
    if material and isinstance(material.supplier_data, dict):
        for photo in _image_urls(material.supplier_data.get('images', [])):
            if photo not in supplier_photos:
                supplier_photos.append(photo)
    kaspi_photos = _image_urls(material.kaspi_data.get('images', [])) if material and isinstance(material.kaspi_data, dict) else []
    if not kaspi_photos:
        kaspi_photos = _image_urls(kaspi_source.get("images", []))
    if not kaspi_photos:
        kaspi_photos = _image_urls(firsthand.get("images", []))
    # Manually pasted screenshot evidence is kept separate from product photos.
    manual_screenshots = []
    if material and isinstance(material.kaspi_data, dict):
        manual_screenshots = [
            x for x in material.kaspi_data.get('manual_screenshots', [])
            if isinstance(x, str) and x.startswith('data:image/jpeg;base64,')
        ][:3]
    target = request.GET.get("next", "")
    parsed = urlsplit(target)
    list_path = reverse("procurement:dashboard")
    if parsed.scheme or parsed.netloc or parsed.path != list_path:
        target = list_path + "?mode=best"
    kaspi_url = ((audit.card_url if audit else "") or
                 (candidate.url if candidate else "") or
                 (_first(kaspi_source, "url") if kaspi_source else "") or
                 (record.kaspi_url if not source_file else ""))
    return render(request, "procurement/eep_comparison.html",
                  context(request, offer=offer, rows=rows,
                          supplier_photo=supplier_photos[0] if supplier_photos else "",
                          supplier_gallery=supplier_photos[1:],
                          kaspi_photo=kaspi_photos[0] if kaspi_photos else "",
                          kaspi_gallery=kaspi_photos[1:],
                          omega_url=offer.supplier_url or offer.supplier.website,
                          kaspi_url=kaspi_url, list_url=target, selected_product_id=product_id,
                          comparison_source=source_file or (audit.source_filename if audit else ""),
                          material=material, manual_screenshots=manual_screenshots,
                          clipboard_pilot=(offer.pk in FIRST_TEN_OFFER_IDS),
                          comparison_first_ten_url=reverse('procurement:comparison_first_ten'),
                          title=f"{offer.brand} {offer.article} — сравнение"))


# Preserve the previously published EEP link as a redirect to the unified view.
@access()
def eep_comparison(request):
    offer = get_object_or_404(Offer, brand__iexact="EEP", article__iexact="59A200068")
    from django.shortcuts import redirect
    url = reverse("procurement:comparison", args=[offer.pk])
    if request.META.get("QUERY_STRING"):
        url += "?" + request.META["QUERY_STRING"]
    return redirect(url)
