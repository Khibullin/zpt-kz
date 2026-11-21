"""Fixed second pilot queue: next 50 profitable Omega SKUs after the first 10.

Immutable offer IDs are taken from the audited November 21 ranking.
This is a comparison/preparation queue, not a purchase approval.
"""
from django.db.models import OuterRef, Subquery
from django.shortcuts import render
from django.urls import reverse

from .models import Offer, KaspiPriceAudit, BuyingComparisonMaterial
from .views import access, context

NEXT_FIFTY_OFFER_IDS = (
    7981, 8138, 13566, 5551, 7986, 15706, 11780, 15627, 8139, 19372,
    15796, 11801, 15630, 15803, 15731, 4978, 11808, 5448, 18750, 15779,
    18765, 18764, 15628, 15725, 15724, 8659, 15714, 15775, 4866, 4930,
    5264, 17559, 18751, 7270, 17751, 10537, 4606, 7644, 13861, 8635,
    10601, 8260, 19473, 8764, 5554, 3106, 19447, 10597, 17566, 17565,
)


@access()
def comparison_next_fifty(request):
    audits = KaspiPriceAudit.objects.filter(offer_id=OuterRef('pk')).order_by(
        '-observed_at', '-pk')
    offers = (Offer.objects.filter(
        pk__in=NEXT_FIFTY_OFFER_IDS, supplier__name='Omega',
        permanent_buying_item__isnull=False
    ).annotate(
        chosen_source=Subquery(audits.values('source_filename')[:1]),
        chosen_product=Subquery(audits.values('product_id')[:1]),
        chosen_price=Subquery(audits.values('min_price')[:1]),
        chosen_roi=Subquery(audits.values('roi')[:1]),
        chosen_profit=Subquery(audits.values('profit')[:1]),
        chosen_checked=Subquery(audits.values('observed_at')[:1]),
    ).order_by('-chosen_roi', '-chosen_profit', 'pk'))
    material_index = {
        (m.offer_id, m.source_filename, m.kaspi_product_id): m
        for m in BuyingComparisonMaterial.objects.filter(
            offer_id__in=NEXT_FIFTY_OFFER_IDS
        ).only('offer_id', 'source_filename', 'kaspi_product_id',
               'supplier_status', 'kaspi_status', 'kaspi_data')
    }
    items = []
    for offer in offers:
        material = material_index.get((
            offer.pk, offer.chosen_source or '', offer.chosen_product or ''))
        supplier_status = material.supplier_status if material else 'pending'
        kaspi_status = material.kaspi_status if material else 'pending'
        screenshots = (
            material.kaspi_data.get('manual_screenshots', [])
            if material and isinstance(material.kaspi_data, dict) else [])
        # All card prices are observations, never current prices or execution proof.
        status = ('Характеристики Kaspi проверены по скриншоту'
                  if kaspi_status == 'manual_verified'
                  else 'Скриншоты ждут подтверждения'
                  if screenshots else 'Требуется дополнить Kaspi')
        items.append({
            'offer': offer, 'source': offer.chosen_source or '',
            'price': offer.chosen_price, 'roi': offer.chosen_roi,
            'profit': offer.chosen_profit, 'checked_at': offer.chosen_checked,
            'supplier_ready': supplier_status == 'complete',
            'status': status, 'screenshots': len(screenshots),
            'url': reverse('procurement:comparison', args=[offer.pk]),
        })
    return render(
        request, 'procurement/comparison_next_fifty.html',
        context(request, items=items,
                total=len(items),
                supplier_ready=sum(1 for item in items if item['supplier_ready']),
                title='Следующие 50 выгодных — Подробности Omega / Kaspi'),
    )
