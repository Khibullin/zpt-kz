"""A fixed, auditable first pilot group from Omega's latest profitable Kaspi price checks.

The IDs are a review queue, not a purchase authorization. No prices are
refreshed or financial records modified by viewing this page.
"""
from django.db.models import OuterRef, Subquery
from django.shortcuts import render
from django.urls import reverse

from .models import Offer, KaspiPriceAudit, BuyingComparisonMaterial
from .views import access, context

FIRST_TEN_OFFER_IDS = (8157, 8114, 8302, 2191, 1340, 8229, 1738, 8180, 8241, 9348)


@access()
def comparison_first_ten(request):
    audits = KaspiPriceAudit.objects.filter(offer_id=OuterRef('pk')).order_by('-observed_at', '-pk')
    offers = (Offer.objects.filter(pk__in=FIRST_TEN_OFFER_IDS, supplier__name='Omega')
              .annotate(source_name=Subquery(audits.values('source_filename')[:1]),
                        chosen_product=Subquery(audits.values('product_id')[:1]),
                        chosen_price=Subquery(audits.values('min_price')[:1]),
                        chosen_roi=Subquery(audits.values('roi')[:1]),
                        chosen_profit=Subquery(audits.values('profit')[:1]))
              .order_by('-chosen_roi', '-chosen_profit', 'pk'))
    items = []
    for offer in offers:
        material = BuyingComparisonMaterial.objects.filter(
            offer=offer, source_filename=offer.source_name or '',
            kaspi_product_id=offer.chosen_product or '').first()
        status = getattr(material, 'kaspi_status', 'pending')
        shots = (material.kaspi_data.get('manual_screenshots', [])
                 if material and isinstance(material.kaspi_data, dict) else [])
        # A stored price or matching title does not prove fitment, kit or execution.
        label = ('Характеристики сверены по скриншоту'
                 if status == 'manual_verified'
                 else 'Есть скриншоты — нужна проверка'
                 if shots else 'Нужны характеристики Kaspi')
        items.append({
            'offer': offer,
            'source': offer.source_name or '',
            'price': offer.chosen_price,
            'roi': offer.chosen_roi,
            'profit': offer.chosen_profit,
            'status': label,
            'screenshots': len(shots),
            'url': reverse('procurement:comparison', args=[offer.pk]),
        })
    return render(request, 'procurement/comparison_first_ten.html',
                  context(request, items=items, title='Первые 10 выгодных — сравнение Omega / Kaspi'))
