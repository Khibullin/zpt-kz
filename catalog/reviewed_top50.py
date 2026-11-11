"""Bounded owner-reviewed Phaeton enrichment; no remote fetches or trade writes."""
import base64
import hashlib
import json
import shutil
from functools import lru_cache
from pathlib import Path

BATCH = 'phaeton-top50-20261111'
MANIFEST_SHA256 = '1d4b8c9836b41753324199c65f7fcd15f9f608d57179a0bfdef2f0bc30535381'


@lru_cache(maxsize=1)
def reviewed_rows():
    raw = (Path(__file__).parent / 'data' / 'reviewed_top50_20261111.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Reviewed top-50 manifest changed')
    payload = json.loads(raw)
    if payload['checked_on'] != '2026-11-11' or len(payload['sources']) != 38:
        raise ValueError('Unexpected reviewed source batch')
    rows = payload['sources']
    if len({r['offer_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate reviewed offer')
    return rows


def exact_offer(offer, row):
    return bool(offer and offer.pk == row['offer_id'] and offer.supplier_id == 3
                and offer.brand == row['brand'] and offer.article == row['article'])


def stage_review(apps, schema_editor):
    """Only add absent fitments and stage already downloaded exact photographs."""
    if schema_editor.connection.alias != 'default':
        return
    from django.db import transaction
    Offer = apps.get_model('procurement', 'Offer')
    Fitment = apps.get_model('procurement', 'VehicleFitment')
    Listing = apps.get_model('procurement', 'RetailListing')
    Task = apps.get_model('catalog', 'SupplierPhotoTask')
    Activity = apps.get_model('procurement', 'Activity')
    Supplier = apps.get_model('procurement', 'Supplier')
    if not Supplier.objects.filter(pk=3, name='Фаетон').exists():
        return
    counts = {'fitment_offers': 0, 'make_model_pairs': 0, 'photo_tasks': 0}
    with transaction.atomic():
        for row in reviewed_rows():
            offer = Offer.objects.select_for_update().filter(pk=row['offer_id']).first()
            if not exact_offer(offer, row):
                continue
            if row.get('fitments') and not Fitment.objects.filter(offer_id=offer.pk).exists():
                for fit in row['fitments']:
                    config = {k: v for k, v in fit.items() if k not in ('make', 'model')}
                    config['source_url'] = row['source_url']
                    Fitment.objects.create(offer_id=offer.pk, make=fit['make'], model=fit['model'],
                        configurations=[config], source_url=row['source_url'], verified=True)
                meta = dict(offer.characteristics or {})
                meta['manufacturer_applicability'] = {
                    'manufacturer': row['brand'], 'checked_on': '2026-11-11',
                    'source_url': row['source_url'], 'status': 'sourced',
                    'source_article': row['article'], 'catalogue_sku': row['source_article'],
                    'source_brand': row['brand'], 'note': row['fitment_note']}
                offer.characteristics = meta
                offer.save(update_fields=['characteristics'])
                counts['fitment_offers'] += 1
                counts['make_model_pairs'] += len(row['fitments'])
            if not row.get('main_b64'):
                continue
            listing = Listing.objects.select_related('product').filter(offer_id=offer.pk, enabled=True).first()
            if not listing or listing.product.status != 'active' or listing.product.article != row['article'] or listing.product.main_image:
                continue
            task = Task.objects.select_for_update().select_related('offer').filter(product_id=listing.product_id).first()
            if task and (task.offer.brand != row['brand'] or task.offer.article != row['article']):
                continue
            if task is None:
                task = Task(product_id=listing.product_id, offer_id=offer.pk)
            task.offer_id = offer.pk
            task.source_url = row.get('image_source_url', row['source_url'])
            task.status = 'reviewed50_ready'
            task.note = 'Точное фото проверено и загружено; ожидает локального сохранения.'
            task.evidence = {**(task.evidence or {}), 'reviewed_batch': BATCH,
                             'reviewed_manifest_sha256': MANIFEST_SHA256}
            task.save()
            counts['photo_tasks'] += 1
        Activity.objects.create(user_id=None, action='reviewed_top50_import', details={'batch': BATCH, **counts})


def photo_bytes(row, key):
    data = base64.b64decode(row[key + '_b64'], validate=True)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != row[key + '_sha256']:
        raise ValueError('Reviewed photograph checksum mismatch')
    return data


def process_reviewed_photos(limit=10):
    """Write packaged images on the running service, where media storage is mounted."""
    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone
    from catalog.models import Product, SupplierPhotoTask
    from procurement.models import Offer, RetailListing
    from catalog.source_photos import save_shared_image
    manifest = {row['offer_id']: row for row in reviewed_rows() if row.get('main_b64')}
    task_ids = list(SupplierPhotoTask.objects.filter(status__in=['reviewed50_ready', 'top50_v2_ready'],
        evidence__reviewed_batch=BATCH).order_by('pk').values_list('pk', flat=True)[:max(0, limit)])
    done = 0
    for task_id in task_ids:
        with transaction.atomic():
            task = SupplierPhotoTask.objects.select_for_update(skip_locked=True, of=('self',)).select_related('offer', 'product').filter(pk=task_id, status__in=['reviewed50_ready', 'top50_v2_ready']).first()
            if task is None:
                continue
            row = manifest.get(task.offer_id)
            listing = RetailListing.objects.filter(product_id=task.product_id, offer_id=task.offer_id, enabled=True).first()
            product = Product.objects.select_for_update().get(pk=task.product_id)
            if (not row or not listing or not exact_offer(task.offer, row)
                    or product.article != row['article'] or product.status != 'active'
                    or task.source_url != row.get('image_source_url', row['source_url'])
                    or task.evidence.get('reviewed_manifest_sha256') != MANIFEST_SHA256):
                task.status = 'skipped'
                task.note = 'Карточка изменилась; адресное добавление отменено.'
            elif product.main_image:
                task.status = 'skipped'
                task.note = 'Существующая фотография сохранена.'
            else:
                main, thumb = photo_bytes(row, 'main'), photo_bytes(row, 'thumbnail')
                media = Path(settings.MEDIA_ROOT)
                media.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(media).free
                if free < len(main) + len(thumb) + 64 * 1024 * 1024:
                    task.status = 'blocked'
                    task.note = 'Недостаточно свободного места; тариф не изменён.'
                    task.evidence = {**task.evidence, 'block_kind': 'storage', 'free_disk_bytes': free}
                else:
                    product.main_image.name = save_shared_image(product.main_image.storage, main)
                    product.supplier_thumbnail.name = save_shared_image(product.supplier_thumbnail.storage, thumb)
                    product.updated_at = timezone.now()
                    product.save(update_fields=['main_image', 'supplier_thumbnail', 'updated_at'])
                    Offer.objects.filter(pk=task.offer_id, photo_url__in=['', row['image_url']]).update(photo_url=product.main_image.url)
                    task.status = 'done'
                    task.note = row.get('image_note', 'Точное фото производителя проверено и сохранено вместе с миниатюрой.')
                    task.evidence = {**task.evidence, 'source_url': row.get('image_source_url', row['source_url']),
                        'image_url': row['image_url'], 'brand': row['brand'], 'article': row['article'],
                        'source_sha256': row['source_sha256'], 'main_path': product.main_image.name,
                        'thumbnail_path': product.supplier_thumbnail.name,
                        'main_size': row['main_size'], 'thumbnail_size': row['thumbnail_size'],
                        'main_bytes': len(main), 'thumbnail_bytes': len(thumb),
                        'checked_at': timezone.now().isoformat()}
            task.save(update_fields=['status', 'note', 'evidence', 'updated_at'])
            done += 1
            if task.status == 'blocked':
                break
    return done
