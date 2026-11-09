"""Durable, resumable supplier catalogue updates; supplier files never renew Kaspi facts."""
import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .analysis import stock_status
from .importing import load_rows, money
from .matching import compare_identity
from .models import (Activity, Offer, Supplier, PriceUpdate, KaspiReviewTask,
                     KaspiSellerSnapshot)

BATCH_SIZE = 200


def prepare_rows(file):
    raw = load_rows(file, max_rows=150000)
    result, seen = [], {}
    for number, row in raw:
        try:
            identity = {key: str(row.get(key) or '').strip() for key in ('brand', 'article', 'name')}
            if not all(identity.values()):
                raise ValueError('Нужны бренд, артикул и название.')
            key = (identity['brand'], identity['article'])
            # A missing stock column must not silently erase confirmed stock.
            if 'stock' not in row:
                raise ValueError('Не найдена колонка остатка stock / Остаток.')
            minimum = Decimal(str(row.get('min_order') or 1))
            if not minimum.is_finite() or minimum != int(minimum) or not 1 <= minimum <= 2147483647:
                raise ValueError('Минимальная партия должна быть положительным целым числом.')
            item = {**identity, 'cost': str(money(row['cost'])) if row['cost'] is not None and str(row['cost']).strip() else '0',
                    'stock': str(row['stock'] if row['stock'] is not None else '').strip(),
                    'min_order': int(minimum)}
            if row['cost'] is None or not str(row['cost']).strip():
                item['_conflict'] = 'В прайсе отсутствует закупочная цена'
            if row.get('Количество в упаковке') is not None:
                pack = Decimal(str(row['Количество в упаковке']))
                if not pack.is_finite() or pack != int(pack) or pack <= 0:
                    raise ValueError('Некорректное количество в упаковке.')
                item['_pack_size'] = int(pack)
            # Only supplier-owned fields are accepted; Kaspi columns are ignored.
            for field in ('category', 'supplier_url'):
                if field in row:
                    item[field] = str(row[field] or '').strip()
            test = Offer(supplier_id=1, **{k:v for k,v in item.items() if not k.startswith('_')})
            test.full_clean(exclude=['supplier'], validate_unique=False, validate_constraints=False)
            if key in seen:
                previous = seen[key]
                if {k:v for k,v in previous.items() if k != '_conflict'} != {k:v for k,v in item.items() if k != '_conflict'}:
                    previous['_conflict'] = 'Несколько строк с разными ценами, наличием или исполнением'
                continue
            seen[key] = item
            result.append(item)
        except (ValidationError, ValueError, TypeError, ArithmeticError) as error:
            raise ValueError(f'Строка {number}: {error}') from error
    return result


@transaction.atomic
def stage_update(file, supplier, user, date, full_catalog=False):
    if date > timezone.localdate():
        raise ValueError('Дата прайса не может быть в будущем.')
    Supplier.objects.select_for_update().get(pk=supplier.pk)
    if PriceUpdate.objects.filter(supplier=supplier, status__in=['ready', 'running']).exists():
        raise ValueError('У поставщика есть незавершённая загрузка. Продолжите её.')
    newest = Offer.objects.filter(supplier=supplier).aggregate(value=Max('supplier_checked_on'))['value']
    if (newest and newest > date) or PriceUpdate.objects.filter(supplier=supplier, checked_on__gt=date).exists():
        raise ValueError('Уже загружен более новый прайс этого поставщика.')
    digest = hashlib.sha256(file.read()).hexdigest()
    file.seek(0)
    previous = PriceUpdate.objects.filter(supplier=supplier, sha256=digest, checked_on=date, full_catalog=full_catalog, status='done').first()
    if previous:
        return previous
    try:
        rows = prepare_rows(file)
    except (KeyError, OSError) as error:
        raise ValueError('Файл Excel повреждён или содержит неподдерживаемую структуру. Сохраните его заново как XLSX или CSV.') from error
    if full_catalog:
        existing = Offer.objects.filter(supplier=supplier).count()
        if existing > 100 and len(rows) < existing / 2:
            raise ValueError('Полный прайс содержит менее половины текущего каталога. Проверьте файл или используйте частичное обновление.')
    batch = PriceUpdate.objects.create(supplier=supplier, user=user, filename=file.name[:255],
        sha256=digest, checked_on=date, full_catalog=full_catalog, rows=rows, row_count=len(rows),
        summary={'new': 0, 'changed': 0, 'unchanged': 0, 'unavailable': 0, 'queued': 0, 'reused': 0, 'conflicts': 0,
                 'conflict_items': [{k:r[k] for k in ('brand', 'article', 'name')} for r in rows if r.get('_conflict')][:100]})
    Activity.objects.create(user=user, action='price_update_staged', details={
        'batch': batch.pk, 'supplier': supplier.pk, 'rows': len(rows), 'sha256': digest,
        'full_catalog': full_catalog})
    return batch


def reuse_identity(offer):
    """Reuse only directly sourced proof for the identical supplier name/variant."""
    candidates = Offer.objects.filter(brand=offer.brand, article=offer.article,
        name=offer.name, match_verified=True).exclude(pk=offer.pk).exclude(kaspi_url='')
    valid = []
    for candidate in candidates:
        proof = (candidate.characteristics or {}).get('kaspi_identity', {})
        if candidate.characteristics.get('supplier_pack_size', 1) != offer.characteristics.get('supplier_pack_size', 1):
            continue
        if not proof.get('confirmed') or proof.get('source_url') != candidate.kaspi_url:
            continue
        confirmed, _ = compare_identity(offer, proof.get('title'), proof.get('article'), proof.get('brand'))
        if confirmed:
            valid.append((candidate, proof))
    if not valid or len({candidate.kaspi_url for candidate, _ in valid}) != 1:
        return False
    candidate, proof = valid[0]
    offer.kaspi_url = candidate.kaspi_url
    offer.match_verified = True
    offer.characteristics = {**offer.characteristics, 'kaspi_identity': {
        **proof, 'reused_from_offer': candidate.pk}}
    # Reuse the observation date, never today's date. Demand is not copied.
    offer.kaspi_price = candidate.kaspi_price
    offer.kaspi_price_checked_on = candidate.kaspi_price_checked_on
    offer.save(update_fields=['kaspi_url', 'match_verified', 'characteristics',
                             'kaspi_price', 'kaspi_price_checked_on'])
    snapshot = KaspiSellerSnapshot.objects.filter(offer=candidate, source_url=candidate.kaspi_url).first()
    if snapshot:
        KaspiSellerSnapshot.objects.update_or_create(offer=offer, defaults={
            'source_url': snapshot.source_url, 'city': snapshot.city, 'checked_on': snapshot.checked_on,
            'complete': snapshot.complete, 'sellers': snapshot.sellers})
    return True


@transaction.atomic
def advance_update(batch_id, prepared_rows=None):
    supplier_id = PriceUpdate.objects.values_list('supplier_id', flat=True).get(pk=batch_id)
    Supplier.objects.select_for_update().get(pk=supplier_id)
    batch = PriceUpdate.objects.select_for_update().defer('rows').get(pk=batch_id)
    if batch.status == 'done':
        return batch
    batch.status = 'running'
    all_rows = prepared_rows if prepared_rows is not None else batch.rows
    rows = all_rows[batch.cursor:batch.cursor + BATCH_SIZE]
    brands = {row['brand'] for row in rows}
    articles = {row['article'] for row in rows}
    existing = {(o.brand, o.article): o for o in Offer.objects.filter(
        supplier=batch.supplier, brand__in=brands, article__in=articles)}
    creates, updates, affected, invalidated = [], [], [], []
    update_fields = ['name', 'category', 'supplier_url', 'cost', 'stock', 'min_order',
                     'supplier_checked_on', 'match_verified', 'characteristics']
    for row in rows:
        offer = existing.get((row['brand'], row['article']))
        if row.get('_conflict'):
            batch.summary['conflicts'] += 1
            if offer:
                Offer.objects.filter(pk=offer.pk).update(stock='')
            continue
        item = {**{k:v for k,v in row.items() if not k.startswith('_')}, 'cost': Decimal(row['cost'])}
        pack = row.get('_pack_size')
        if offer is None:
            offer = Offer(supplier=batch.supplier, **item, supplier_checked_on=batch.checked_on)
            if pack is not None:
                offer.characteristics['supplier_pack_size'] = pack
            creates.append(offer)
            batch.summary['new'] += 1
        else:
            if offer.supplier_checked_on and offer.supplier_checked_on > batch.checked_on:
                raise ValueError('Данные поставщика уже новее этого прайса.')
            changed = any(getattr(offer, key) != value for key, value in item.items())
            identity_changed = offer.name != item['name'] or (pack is not None and offer.characteristics.get('supplier_pack_size', 1) != pack)
            if pack is not None:
                offer.characteristics = {**offer.characteristics, 'supplier_pack_size': pack}
            for key, value in item.items():
                setattr(offer, key, value)
            offer.supplier_checked_on = batch.checked_on
            if identity_changed:
                offer.match_verified = False
                offer.characteristics = {k: v for k, v in offer.characteristics.items() if k != 'kaspi_identity'}
                invalidated.append(offer.pk)
            updates.append(offer)
            batch.summary['changed' if changed else 'unchanged'] += 1
        affected.append(offer)
    Offer.objects.bulk_create(creates, batch_size=BATCH_SIZE)
    Offer.objects.bulk_update(updates, update_fields, batch_size=BATCH_SIZE)
    KaspiSellerSnapshot.objects.filter(offer_id__in=invalidated).delete()
    today = timezone.localdate()
    for offer in affected:
        if 'подшипник' in (offer.name + ' ' + offer.category).casefold() or 'bearing' in (offer.name + ' ' + offer.category).casefold():
            continue
        if stock_status(offer.stock) != 'in_stock':
            KaspiReviewTask.objects.filter(offer=offer).update(status='done', reason='Нет подтверждённого наличия')
            continue
        if not offer.kaspi_url and not offer.match_verified and reuse_identity(offer):
            batch.summary['reused'] += 1
        price_fresh = offer.kaspi_price_checked_on and today - timedelta(days=7) <= offer.kaspi_price_checked_on <= today
        snapshot = KaspiSellerSnapshot.objects.filter(offer=offer, source_url=offer.kaspi_url, city='750000000', complete=True, checked_on__gte=today-timedelta(days=7), checked_on__lte=today).exists()
        if not offer.match_verified or not price_fresh or not snapshot:
            reason = 'Найти и проверить карточку Kaspi' if not offer.kaspi_url else (
                'Проверить соответствие карточки' if not offer.match_verified else 'Обновить цену и продавцов Kaspi')
            KaspiReviewTask.objects.update_or_create(offer=offer, defaults={
                'batch': batch, 'reason': reason, 'priority': 1 if offer.match_verified else 3, 'status': 'pending'})
            batch.summary['queued'] += 1
    batch.cursor += len(rows)
    if batch.cursor >= batch.row_count:
        if batch.full_catalog:
            # Only finalize after every validated row was applied. Never act on a partial upload.
            keys = {(row['brand'], row['article']) for row in all_rows}
            absent = [o.pk for o in Offer.objects.filter(supplier=batch.supplier).only('pk', 'brand', 'article')
                      if (o.brand, o.article) not in keys]
            for start in range(0, len(absent), 500):
                Offer.objects.filter(pk__in=absent[start:start+500]).update(stock='0', supplier_checked_on=batch.checked_on)
                KaspiReviewTask.objects.filter(offer_id__in=absent[start:start+500]).update(status='done', reason='Нет в полном прайсе')
            batch.summary['unavailable'] = len(absent)
        batch.status = 'done'
        Activity.objects.create(user=batch.user, action='price_update_done', details={'batch': batch.pk, **batch.summary})
    batch.save(update_fields=['cursor', 'status', 'summary', 'updated_at'])
    return batch
