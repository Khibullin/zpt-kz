"""Short-lived, non-personal catalogue dictionaries shared between tab visits."""
from functools import wraps
from uuid import uuid4

from django.core.cache import caches
from django.db import transaction, connection
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import Offer, VehicleFitment


REVISION_KEY = 'procurement:filter-options:revision'
cache = caches['procurement_options']
OPTIONS_TTL = 600


def invalidate_options():
    cache.set(REVISION_KEY, uuid4().hex, timeout=None)


@receiver(post_save, sender=Offer)
@receiver(post_delete, sender=Offer)
@receiver(post_save, sender=VehicleFitment)
@receiver(post_delete, sender=VehicleFitment)
def changed_options(sender, update_fields=None, **kwargs):
    # Photos, prices and stock do not change the filter dictionaries. Frequent
    # enrichment writes must not force every visitor to rescan the catalogue.
    fields = {'category', 'supplier', 'supplier_id'} if sender is Offer else {
        'offer', 'offer_id', 'make', 'model', 'verified', 'configurations'}
    if update_fields is not None and fields.isdisjoint(update_fields):
        return
    invalidate_options()
    transaction.on_commit(invalidate_options)


def refresh_options_after_import(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        result = function(*args, **kwargs)
        invalidate_options()
        transaction.on_commit(invalidate_options)
        return result
    return wrapped


def options_cache_allowed():
    return not connection.in_atomic_block


def supplier_options(supplier_id):
    # TTL also bounds staleness for external bulk writers and other worker processes.
    key = f'procurement:filter-options:v1:{cache.get(REVISION_KEY, "0")}:{supplier_id or "all"}'
    use_cache = options_cache_allowed()
    result = cache.get(key) if use_cache else None
    if result is None:
        offers = Offer.objects.all()
        fitments = VehicleFitment.objects.filter(verified=True)
        if supplier_id:
            offers = offers.filter(supplier_id=supplier_id)
            fitments = fitments.filter(offer__supplier_id=supplier_id)
        categories = list(offers.exclude(category='').order_by('category').values_list('category', flat=True).distinct())
        models = {}
        for make, model in fitments.order_by('make', 'model').values_list('make', 'model').distinct():
            models.setdefault(make, []).append(model)
        result = (categories, models)
        if use_cache:
            cache.set(key, result, timeout=OPTIONS_TTL)
    return result


def supplier_years(supplier_id):
    from .fitment_filters import configuration_years
    from django.utils import timezone
    key = f'procurement:years:v2:{timezone.localdate().year}:{cache.get(REVISION_KEY, "0")}:{supplier_id or "all"}'
    result = cache.get(key) if options_cache_allowed() else None
    if result is None:
        query = VehicleFitment.objects.filter(verified=True)
        if supplier_id:
            query = query.filter(offer__supplier_id=supplier_id)
        values = set()
        if connection.vendor == 'postgresql':
            # Extract distinct year ranges in PostgreSQL, without transferring
            # hundreds of thousands of full configurations to the web worker.
            sql, params = query.order_by().values('configurations').distinct().query.sql_with_params()
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT DISTINCT item->>'years' FROM ({sql}) vf "
                               f"CROSS JOIN LATERAL jsonb_array_elements(vf.configurations) item", params)
                values.update(str(row[0] or '') for row in cursor.fetchall())
        else:
            for configs in query.values_list('configurations', flat=True).iterator(chunk_size=100):
                for config in configs:
                    if isinstance(config, dict):
                        values.add(str(config.get('years', '')))
        result = sorted({year for value in values for year in configuration_years(value)}, reverse=True)
        if options_cache_allowed():
            cache.set(key, result, timeout=OPTIONS_TTL)
    return result


def supplier_generations(supplier_id, make, model):
    if not make or not model:
        return []
    from hashlib import sha256
    scope = sha256(f'{supplier_id}:{make}:{model}'.encode()).hexdigest()
    key = f'procurement:generations:v1:{cache.get(REVISION_KEY, "0")}:{scope}'
    use_cache = options_cache_allowed()
    result = cache.get(key) if use_cache else None
    if result is None:
        query = VehicleFitment.objects.filter(verified=True, make=make, model=model)
        if supplier_id:
            query = query.filter(offer__supplier_id=supplier_id)
        result = sorted({config['generation']
                         for configs in query.order_by().values_list('configurations', flat=True).distinct().iterator(chunk_size=100)
                         for config in configs
                         if isinstance(config, dict) and config.get('generation')})
        if use_cache:
            cache.set(key, result, timeout=OPTIONS_TTL)
    return result
