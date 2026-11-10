"""Reuse reviewed applications for the same manufacturer and complete article."""
import json
from copy import deepcopy
from urllib.parse import urlsplit

from django.db import transaction
from .models import Offer, VehicleFitment, Activity


def identity(brand, article):
    # Punctuation and suffixes remain significant; no OEM or cross-brand matching.
    return brand.strip().upper(), article.strip().upper()


@transaction.atomic
def fill_from_shared_base(supplier, user):
    sources = {}
    for fit in VehicleFitment.objects.filter(verified=True).exclude(source_url='').select_related('offer').only('offer_id', 'make', 'model', 'details', 'configurations', 'source_url', 'offer__brand', 'offer__article').iterator(chunk_size=500):
        if urlsplit(fit.source_url).scheme != 'https':
            continue
        key = identity(fit.offer.brand, fit.offer.article)
        sources.setdefault(key, {}).setdefault(fit.offer_id, []).append(fit)
    candidates = Offer.objects.filter(supplier=supplier).exclude(fitments__isnull=False)
    updated = pairs = conflicts = 0
    for offer in candidates.iterator(chunk_size=200):
        choices = sources.get(identity(offer.brand, offer.article), {})
        if not choices:
            continue
        # Different reviewed sets can indicate a correction or a restricted variant.
        signatures = {json.dumps(sorted((f.make, f.model, f.details,
                        json.dumps(f.configurations, sort_keys=True, ensure_ascii=False)) for f in fits),
                        ensure_ascii=False) for fits in choices.values()}
        if len(signatures) != 1:
            conflicts += 1
            continue
        fits = next(iter(choices.values()))
        # Lock the target and recheck: concurrent clicks/imports must not duplicate rows.
        Offer.objects.select_for_update().get(pk=offer.pk)
        if VehicleFitment.objects.filter(offer=offer).exists():
            continue
        VehicleFitment.objects.bulk_create([VehicleFitment(offer=offer, make=f.make,
            model=f.model, details=f.details, configurations=deepcopy(f.configurations),
            source_url=f.source_url, verified=True) for f in fits], batch_size=200)
        metadata = dict(offer.characteristics or {})
        metadata['shared_fitment_source'] = {'offer_id': fits[0].offer_id,
            'source_urls': sorted({f.source_url for f in fits})}
        offer.characteristics = metadata
        offer.save(update_fields=['characteristics'])
        updated += 1
        pairs += len(fits)
    result = {'supplier': supplier.name, 'offers': updated, 'make_model_pairs': pairs,
              'conflicts': conflicts}
    Activity.objects.create(user=user, action='shared_fitment_import', details=result)
    return result
