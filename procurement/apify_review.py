"""Read-only staff review of the exact 14:00 Kaspi/Omega Apify snapshots.

This is NOT the publishable 'Выгодные' list. No automatic approval.
"""
import re
from decimal import Decimal
from django.shortcuts import render
from .models import KaspiPriceAudit, KaspiReviewTask, PermanentBuyingItem
from .views import access, context
from .srr_review_snapshots import SOURCE as SRR_SOURCE, srr_review_snapshot

SOURCE='dataset_kaspi-api_2026-11-21_14-00-09-125.json'

@access('sales','admin')
def apify_review(request):
    audits=(KaspiPriceAudit.objects.filter(source_filename=SOURCE,
        identity_status='needs_review',roi__gte=50)
        .select_related('offer__supplier').order_by('-roi','pk'))
    audits=list(audits)
    tasks={t.offer_id:t for t in KaspiReviewTask.objects.filter(
        offer_id__in=[a.offer_id for a in audits])}
    prior=set(PermanentBuyingItem.objects.filter(
        offer_id__in=[a.offer_id for a in audits]).values_list('offer_id',flat=True))
    rows=[{'audit':a,'offer':a.offer,'task':tasks.get(a.offer_id),
           'previously_listed':a.offer_id in prior} for a in audits]

    # Separate first-export SRR identity mismatches from the second-export
    # ROI candidates: neither list grants verified/publishable status.
    srr_tasks=(KaspiReviewTask.objects.filter(
        reason__startswith='Apify 21.11.2026:')
        .select_related('offer__supplier').order_by('offer__article'))
    srr_rows=[]
    for task in srr_tasks:
        found=re.search(r'Kaspi ID (\d{7,12})', task.reason or '')
        snapshot = srr_review_snapshot(task.offer, SRR_SOURCE)
        cost = task.offer.cost
        roi = None
        if snapshot and cost and cost > 0:
            price = Decimal(snapshot["comparison_min_price"])
            roi = (price * Decimal("0.875") - cost - Decimal("250")) / cost * 100
        srr_rows.append({'offer':task.offer, 'task':task, 'roi':roi,
                         'kaspi_id':found.group(1) if found else '',
                         'kaspi_title':snapshot['title'] if snapshot else '',
                         'kaspi_url':snapshot['url'] if snapshot else '',
                         'kaspi_price':snapshot['comparison_min_price'] if snapshot else None,
                         'cheapest_url':snapshot['cheapest_url'] if snapshot else '',
                         'cheapest_card_differs':snapshot['different_cheapest_card'] if snapshot else False,
                         'has_source_snapshot':bool(snapshot)})
    return render(request,'procurement/apify_review.html',
        context(request,rows=rows,prior_count=len(prior),srr_rows=srr_rows,
                source_file=SOURCE,srr_source=SRR_SOURCE,
                srr_profitable_count=sum(1 for row in srr_rows if row['roi'] is not None and row['roi'] >= 50),
                title='Apify — проверка выгрузок 21.11'))
