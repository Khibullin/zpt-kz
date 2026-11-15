"""Non-personal filter dictionaries, built outside visitor requests."""
from collections import defaultdict
from datetime import timedelta
from django.core.cache import caches
from django.db import connection
from django.utils import timezone

PREFIX = 'proc-options:'


def snapshot_options(supplier_id):
    from catalog.models import StorefrontFacetSnapshot
    key = PREFIX + str(supplier_id or 'all')
    cache = caches['procurement_options']
    payload = cache.get(key)
    if payload is None:
        payload = StorefrontFacetSnapshot.objects.filter(pk=key).values_list('payload', flat=True).first() or {}
        cache.set(key, payload, timeout=300)
    return payload


def refresh_filter_snapshots(apps=None, force=False):
    from .filter_options import cache, REVISION_KEY
    from .fitment_filters import configuration_years
    if apps is None:
        from .models import Offer, VehicleFitment, Supplier
        from catalog.models import StorefrontFacetSnapshot as Snapshot
    else:
        Offer = apps.get_model('procurement', 'Offer')
        VehicleFitment = apps.get_model('procurement', 'VehicleFitment')
        Supplier = apps.get_model('procurement', 'Supplier')
        Snapshot = apps.get_model('catalog', 'StorefrontFacetSnapshot')
    revision = cache.get(REVISION_KEY, '0')
    last = Snapshot.objects.filter(pk=PREFIX+'all').first()
    if not force and last and last.built_at:
        age = timezone.now()-last.built_at
        if age < timedelta(minutes=1) or (age < timedelta(minutes=30) and last.payload.get('revision') == revision):
            return False
    # One consumer, one pass through configurations; retain only small dictionaries.
    locked = False
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(88641234)')
            locked = cursor.fetchone()[0]
        if not locked:
            return False
    try:
        buckets = {}
        for identifier in [None, *Supplier.objects.values_list('pk', flat=True)]:
            buckets[str(identifier or 'all')] = dict(categories=set(), models=defaultdict(set), years=set(), generations=defaultdict(lambda: defaultdict(set)))
        for supplier, category in Offer.objects.exclude(category='').order_by().values_list('supplier_id','category').distinct():
            for key in ('all', str(supplier)):
                if key in buckets:
                    buckets[key]['categories'].add(category)
        ranges = {}
        rows = VehicleFitment.objects.filter(verified=True).order_by().values_list('offer__supplier_id','make','model','configurations')
        for supplier, make, model, configs in rows.iterator(chunk_size=100):
            keys = [key for key in ('all', str(supplier)) if key in buckets]
            for key in keys:
                buckets[key]['models'][make].add(model)
            for config in configs if isinstance(configs, list) else []:
                if not isinstance(config, dict):
                    continue
                value = str(config.get('years', ''))
                if value not in ranges:
                    ranges[value] = tuple(configuration_years(value))
                for key in keys:
                    buckets[key]['years'].update(ranges[value])
                    if config.get('generation'):
                        buckets[key]['generations'][make][model].add(config['generation'])
        now = timezone.now()
        # Write complete results atomically; readers keep the previous valid snapshot.
        from django.db import transaction
        with transaction.atomic():
            for key, data in buckets.items():
                payload = dict(version=1, revision=revision, categories=sorted(data['categories']),
                    models={make:sorted(models) for make, models in sorted(data['models'].items())},
                    years=sorted(data['years'],reverse=True),
                    generations={make:{model:sorted(values) for model,values in models.items()} for make,models in data['generations'].items()})
                Snapshot.objects.update_or_create(pk=PREFIX+key, defaults=dict(payload=payload,built_at=now))
        for key in buckets:
            cache.delete(PREFIX+key)
        return True
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(88641234)')
