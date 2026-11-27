"""Separate dated national-minimum review from imported price observations."""
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone


def reviewed_minimum_query(prefix=''):
    now = timezone.now()
    return Q(**{
        prefix + 'identity_status': 'verified',
        prefix + 'minimum_review_status': 'confirmed',
        prefix + 'minimum_reviewed_at__gte': now - timedelta(days=7),
        prefix + 'minimum_reviewed_at__lte': now,
        prefix + 'minimum_review_evidence__scope': 'kazakhstan',
        prefix + 'minimum_review_evidence__search_complete': True,
        prefix + 'minimum_review_evidence__sellers_complete': True,
    })


def blocked_phaeton_offer_ids(offer_ids):
    from .models import KaspiPriceAudit, Offer
    from django.db.models import OuterRef, Subquery
    latest = KaspiPriceAudit.objects.filter(offer_id=OuterRef('pk')).order_by('-observed_at', '-pk')
    offers = Offer.objects.filter(pk__in=offer_ids, supplier__name='Phaeton DC').annotate(
        latest_audit_id=Subquery(latest.values('pk')[:1]))
    allowed = KaspiPriceAudit.objects.filter(reviewed_minimum_query()).values('pk')
    return set(offers.exclude(latest_audit_id__in=allowed).values_list('pk', flat=True))
