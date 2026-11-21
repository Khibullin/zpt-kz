"""Read-only staff review of the exact 14:00 Kaspi/Omega Apify snapshots.

This is NOT the publishable 'Выгодные' list. No automatic approval.
"""
from django.shortcuts import render
from .models import KaspiPriceAudit, KaspiReviewTask, PermanentBuyingItem
from .views import access, context

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
    return render(request,'procurement/apify_review.html',
        context(request,rows=rows,prior_count=len(prior),
                source_file=SOURCE,title='Apify — проверка второй выгрузки'))
