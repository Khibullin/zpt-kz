"""Read-only comparison for the approved EEP 59A200068 sample only."""
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from .models import Offer, KaspiBuyingCandidate, KaspiPriceAudit
from .views import access, context

ARTICLE = "59A200068"
OMEGA_URL = "https://omega-auto-parts.kz/transmissiya/privod-kolesa-r-v-sbore-3024-eep-59a200068"
KASPI_URL = "https://kaspi.kz/shop/p/eep-privod-kolesa-v-sbore-59a200068-179043622/?c=750000000"


def _text(value):
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value if item)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items() if _text(item))
    return str(value).strip()


def _field(source, *keys):
    if not isinstance(source, dict):
        return ""
    for key in keys:
        value = _text(source.get(key))
        if value:
            return value
    return ""


@access()
def eep_comparison(request):
    """No writes: original prices, availability, audit, selection and import remain untouched."""
    offer = get_object_or_404(Offer.objects.select_related("supplier"),
                              brand__iexact="EEP", article__iexact=ARTICLE,
                              supplier__name__icontains="Omega")
    source = offer.characteristics if isinstance(offer.characteristics, dict) else {}
    candidate = (KaspiBuyingCandidate.objects.filter(product_id="179043622", offer=offer).first()
                 or KaspiBuyingCandidate.objects.filter(product_id="179043622").first())
    source_file = request.GET.get("apify_source", "")[:255]
    audits = KaspiPriceAudit.objects.filter(offer=offer, product_id="179043622")
    if source_file:
        audits = audits.filter(source_filename=source_file)
    audit = audits.order_by("-observed_at", "-pk").first()
    fits = offer.fitments.all().order_by("make", "model")[:200]
    fitment = "; ".join(
        " ".join(filter(None, (str(item.make or ""), str(item.model or ""))))
        for item in fits if getattr(item, "verified", False)
    )
    supplier_data = {
        "name": offer.name, "brand": offer.brand, "article": offer.article,
        "category": offer.category, "description": offer.description,
        "specs": _field(source, "technical_characteristics", "specifications", "technical_specs", "specs"),
        "fitment": fitment or offer.applicability,
        "package": _field(source, "package", "packaging", "completeness", "kit_contents"),
        "oem": _field(source, "oem", "oem_numbers", "oe_numbers", "original_numbers"),
        "analogs": _field(source, "analogues", "analogs", "cross_references", "replacements"),
    }
    kaspi_data = {
        "name": (candidate.title if candidate else "") or (audit.card_title if audit else ""),
        "brand": candidate.brand if candidate else "",
        "article": ARTICLE if candidate and candidate.product_id == "179043622" and candidate.brand.upper() == "EEP" else "",
        "category": "", "description": "", "specs": "", "fitment": "",
        "package": "", "oem": "", "analogs": "",
    }
    labels = (
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
    rows = []
    for key, label in labels:
        left, right = _text(supplier_data[key]), _text(kaspi_data[key])
        different = bool(left and right and left.casefold() != right.casefold())
        rows.append({"label": label, "supplier": left or "Нет данных",
                     "kaspi": right or "Нет данных", "different": different})
    target = request.GET.get("next", "")
    parsed = urlsplit(target)
    list_path = reverse("procurement:dashboard")
    if parsed.scheme or parsed.netloc or parsed.path != list_path:
        target = list_path + "?mode=best"
    return render(request, "procurement/eep_comparison.html",
                  context(request, offer=offer, rows=rows, supplier_photo=offer.photo_url,
                          kaspi_photo="", omega_url=OMEGA_URL, kaspi_url=KASPI_URL,
                          list_url=target, title="EEP 59A200068 — сравнение"))
