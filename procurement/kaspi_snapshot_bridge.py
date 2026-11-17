"""Reuse existing repricer snapshots only for EXACT, independently verified Kaspi cards.

The office collector is not a guaranteed complete seller list. Shared snapshots
are deliberately provisional and NEVER renew Offer.kaspi_price_checked_on or
make an offer eligible for automatic profitable purchasing.
"""
from datetime import timedelta
from django.utils import timezone

from catalog.kaspi_public_url import kaspi_product_id_from_public_url
from catalog.models import ProductKaspiListing
from repricer.models import KaspiCompetitorIngestBatch, KaspiCompetitorOfferSnapshot
from .models import KaspiSellerSnapshot, Offer


def verified_listing_for(offer):
    if not offer.match_verified or not offer.kaspi_url or not offer.article:
        return None
    product_id = kaspi_product_id_from_public_url(offer.kaspi_url)
    if not product_id:
        return None
    candidates = ProductKaspiListing.objects.filter(
        is_active=True, public_url_verified_at__isnull=False,
        product__article__iexact=offer.article,
    ).exclude(public_url='').select_related('product')
    matches = [
        item for item in candidates
        if kaspi_product_id_from_public_url(item.public_url) == product_id
    ]
    # A second, ambiguous listing is never accepted as identity evidence.
    return matches[0] if len(matches) == 1 else None


def sync_provisional_snapshot(offer, *, today=None):
    """Return True only if new incomplete seller evidence was safely reused."""
    today = today or timezone.localdate()
    listing = verified_listing_for(offer)
    if listing is None:
        return False
    batch = (KaspiCompetitorIngestBatch.objects.filter(
        listing=listing, source='office_collector',
        captured_at__date__gte=today - timedelta(days=7),
        captured_at__date__lte=today,
    ).order_by('-captured_at', '-pk').first())
    if batch is None:
        return False
    observed = list(KaspiCompetitorOfferSnapshot.objects.filter(
        listing=listing, captured_at=batch.captured_at,
        source='office_collector', is_available=True,
    ).order_by('price', 'pk')[:100])
    if not observed or any(
        not row.seller_code or not row.seller_name or row.price <= 0
        or not row.seller_code.replace('_','').replace('-','').isalnum()
        for row in observed
    ):
        return False
    existing = KaspiSellerSnapshot.objects.filter(offer=offer).first()
    # Never overwrite a fully verified observation or one from a newer date.
    if existing and (existing.complete or existing.checked_on >= batch.captured_at.date()):
        return False
    sellers = [{
        'merchant_id': row.seller_code,
        'name': row.seller_name,
        'price': str(row.price),
        'url': 'https://kaspi.kz/shop/m/' + row.seller_code + '/',
        'rating': None, 'reviews': None,
    } for row in observed]
    KaspiSellerSnapshot.objects.update_or_create(offer=offer, defaults={
        'source_url': offer.kaspi_url, 'city': '750000000',
        'checked_on': batch.captured_at.date(),
        'complete': False, 'sellers': sellers,
    })
    return True


def sync_verified_preview_batch(limit=10):
    """Bounded database-only pass; no HTTP, image, fitment, price or stock writes."""
    # Narrow the read to exact listed articles before any per-item checks.
    listed_articles = ProductKaspiListing.objects.filter(
        is_active=True, public_url_verified_at__isnull=False,
    ).values('product__article')
    candidates = Offer.objects.filter(
        match_verified=True, article__in=listed_articles,
    ).exclude(kaspi_url='').order_by('pk')[:500]
    done = 0
    for offer in candidates:
        if sync_provisional_snapshot(offer):
            done += 1
            if done >= limit:
                break
    return done
