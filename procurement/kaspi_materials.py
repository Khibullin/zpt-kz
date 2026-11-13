"""Build purchased-card material in the existing background consumer, never over HTTP."""
import hashlib
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Activity, Draft, KaspiPreparedCard, KaspiCardImage, KaspiCardPrice, KaspiPricingRule, Preferences

logger = logging.getLogger(__name__)
PRIVATE_LINE = re.compile(r'(цена|скидк|остаток|себестоим|прайс|опт\d|stock|cost|price)', re.I)
PRIVATE_KEY = re.compile(r'^(omega_|shared_|reviewed_|manufacturer_|supplier_|kaspi_|source|price|cost|stock)', re.I)


def identity_for(batch, row, supplier_id):
    supplier = str(supplier_id) if supplier_id else batch['supplier'].strip().casefold()
    return hashlib.sha256(json.dumps([supplier, row['brand'].strip().casefold(),
        row['article'].strip().casefold()], ensure_ascii=False).encode()).hexdigest()


def customer_description(text, row):
    lines = [line.strip() for line in (text or '').splitlines()
        if line.strip() and not PRIVATE_LINE.search(line)]
    if not lines:
        lines = [row['name'].strip(), f"Бренд: {row['brand']}. Артикул: {row['article']}."]
    return '\n'.join(lines)[:1024]


def fitment_material(fits):
    lines, urls = [], []
    for fit in fits:
        lines.append(' '.join(str(x) for x in (fit.make, fit.model, fit.details) if x))
        for config in fit.configurations:
            if isinstance(config, dict):
                parts = [str(config[k]) for k in ('generation', 'chassis', 'years', 'engine', 'capacity', 'fuel', 'transmission', 'body', 'drive', 'notes') if config.get(k)]
                if parts:
                    lines.append(' · '.join(parts))
        if fit.source_url:
            urls.append(fit.source_url)
    return '\n'.join(dict.fromkeys(lines)), list(dict.fromkeys(urls))


def automatic_values(row):
    offer = row.get('offer')
    legacy = row.get('draft')
    attributes = {str(k): v for k, v in (offer.characteristics if offer else {}).items()
        if not PRIVATE_KEY.search(str(k)) and not PRIVATE_LINE.search(str(k))
        and isinstance(v, (str, int, float, bool)) and v is not None}
    fits, urls = fitment_material(row.get('fitments', []))
    category = legacy.category if legacy and legacy.category.startswith('Master -') else ''
    return dict(brand=row['brand'], article=row['article'],
        title=legacy.title if legacy else offer.name if offer else row['name'],
        description=customer_description(legacy.description if legacy else offer.description if offer else '', row),
        photo_url=legacy.photo_url if legacy else offer.photo_url if offer else '',
        category=category, characteristics=attributes, applicability=fits,
        applicability_source=urls[0] if urls else ''), urls


def snapshot(card):
    return {key: getattr(card, key) for key in ('title', 'description', 'photo_url', 'category',
        'characteristics', 'applicability', 'applicability_source', 'verified', 'revision')} | {
        'image_ids': list(card.images.filter(active=True).values_list('pk', flat=True)) if card.pk else []}


def record(card, user, batch_key, row, action, before=None):
    Activity.objects.create(user=user, action=action, details={
        'kaspi_batch': batch_key, 'card': card.pk, 'line': row['index'],
        'brand': row['brand'], 'article': row['article'], 'before': before or {}, 'after': snapshot(card)})


def prepare_batch(batch, rows, supplier_id, user):
    result = []
    for row in rows:
        if not row['brand'] or not row['article']:
            continue
        key = identity_for(batch, row, supplier_id)
        values, urls = automatic_values(row)
        source = {'batch': batch['key'], 'sources': urls, 'offer_id': row['offer'].pk if row.get('offer') else None}
        with transaction.atomic():
            card = KaspiPreparedCard.objects.select_for_update().filter(identity=key).first()
            if card is None:
                card = KaspiPreparedCard.objects.create(identity=key, offer=row.get('offer'), **values, source=source)
                record(card, user, batch['key'], row, 'kaspi_card_auto')
            else:
                changes = {k: v for k, v in values.items() if k not in card.manual_fields and k not in card.source.get('enriched_fields', []) and getattr(card, k) != v and (k != 'photo_url' or v)}
                if changes:
                    before = snapshot(card)
                    for field, value in changes.items():
                        setattr(card, field, value)
                    card.verified = False
                    card.revision += 1
                    card.offer = row.get('offer')
                    card.source = {**card.source, **source}
                    card.save()
                    record(card, user, batch['key'], row, 'kaspi_card_auto', before)
                # Reused cards retain a per-purchase audit record without duplicate versions.
                if not Activity.objects.filter(action__in=['kaspi_card_auto', 'kaspi_card_reused'],
                        details__kaspi_batch=batch['key'], details__card=card.pk).exists():
                    record(card, user, batch['key'], row, 'kaspi_card_reused')
        result.append((row, card))
    return result


def encode_image(data, require_size=False):
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError('Фотография должна быть не больше 5 МБ.')
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ('JPEG', 'PNG', 'WEBP') or probe.width * probe.height > 25_000_000:
                raise ValueError('Нужна фотография JPG, PNG или WebP подходящего размера.')
            probe.verify()
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original)
            if require_size and min(image.size) < 500:
                raise ValueError('Для Kaspi загрузите исходное фото не меньше 500 × 500 пикселей.')
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            rgba = image.convert('RGBA')
            base = Image.new('RGBA', image.size, 'white')
            base.alpha_composite(rgba)
            image = base.convert('RGB')
            main = io.BytesIO()
            image.save(main, 'JPEG', quality=90, optimize=True)
            thumb = image.copy()
            thumb.thumbnail((180, 180), Image.Resampling.LANCZOS)
            small = io.BytesIO()
            thumb.save(small, 'JPEG', quality=80, optimize=True)
            return main.getvalue(), small.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Файл не является корректной фотографией.') from exc


def store_image(card, encoded, source_url='', replace_id=None):
    data, thumb, width, height = encoded
    digest = hashlib.sha256(data).hexdigest()
    existing = card.images.filter(digest=digest).first()
    old = card.images.filter(pk=replace_id, active=True).first() if replace_id else None
    if replace_id and old is None:
        raise ValueError('Фотография изменена. Обновите карточку.')
    if existing and existing.active:
        return existing
    if not old and card.images.filter(active=True).count() >= 15:
        raise ValueError('В карточке уже 15 фотографий.')
    position = old.position if old else card.images.filter(active=True).count()
    if existing:
        asset = existing
        asset.active = True
        asset.position = position
        asset.save(update_fields=['active', 'position'])
    else:
        asset = KaspiCardImage.objects.create(card=card, digest=digest, data=data, thumbnail=thumb,
            width=width, height=height, source_url=source_url, position=position)
    if old:
        old.active = False
        old.save(update_fields=['active'])
    return asset


def fetch_card_photo(row, card):
    if 'images' in card.manual_fields or card.images.filter(active=True).exists():
        return False
    attempt = card.source.get('photo_attempt', {})
    if attempt.get('url') == card.photo_url and attempt.get('at'):
        try:
            if timezone.now() - datetime.fromisoformat(attempt['at']) < timedelta(hours=24):
                return False
        except ValueError:
            pass
    revision = card.revision
    source_url = card.photo_url
    try:
        if not source_url and row.get('offer') and row['offer'].supplier_url:
            from catalog.source_photos import public_page_url, matching_image, fetch_bytes
            page = public_page_url(row['offer'].supplier_url, row['brand'])
            source_url = matching_image(fetch_bytes(page).decode('utf-8', errors='replace'), row['brand'], row['article'])
        if not source_url:
            raise ValueError('Не найден источник фотографии точного товара.')
        # Existing SSRF-safe downloader checks each redirect and bounds the file size.
        from catalog.remote_image import download_public_image
        data, _ = download_public_image(source_url, timeout=8)
        encoded = encode_image(data)
        with transaction.atomic():
            current = KaspiPreparedCard.objects.select_for_update().get(pk=card.pk)
            if current.revision != revision or 'images' in current.manual_fields or current.images.filter(active=True).exists():
                return False
            store_image(current, encoded, source_url=source_url)
            if 'photo_url' not in current.manual_fields:
                current.photo_url = source_url
            current.source = {**current.source, 'photo_attempt': {'url': current.photo_url, 'at': timezone.now().isoformat(), 'result': 'saved'}}
            current.revision += 1
            current.verified = False
            current.save()
        return True
    except Exception as exc:
        # Failures remain visible and bounded; never retry a rejected source in a loop.
        reason = str(exc)[:200] if isinstance(exc, ValueError) else 'Источник фото недоступен. Нужен другой подтверждённый источник или загрузка фото.'
        current = KaspiPreparedCard.objects.get(pk=card.pk)
        current.source = {**current.source, 'photo_attempt': {'url': card.photo_url, 'at': timezone.now().isoformat(), 'result': reason}}
        current.save(update_fields=['source', 'updated_at'])
        return False


_last_scan = 0.0


def process_purchased_cards(limit=50, fetch_photos=False, throttle=False):
    global _last_scan
    if throttle and time.monotonic() - _last_scan < 30:
        return 0
    _last_scan = time.monotonic()
    from inventory.models import GoodsDocument
    from .models import PurchaseOrder
    from .kaspi_workspace import purchased_batches, source_rows, enrich_rows
    ids = set(PurchaseOrder.objects.filter(archived=False).values_list('user_id', flat=True))
    ids.update(GoodsDocument.objects.filter(kind='receipt', evidence_only=False).values_list('created_by_id', flat=True))
    users = get_user_model().objects.filter(pk__in=ids, is_active=True)
    locked = False
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(88641233)')
            locked = cursor.fetchone()[0]
        if not locked:
            return 0
    count = 0
    fetched = False
    try:
        for user in users:
            from .permissions import workspace_role
            if workspace_role(user) is None:
                continue
            for batch in purchased_batches(user):
                rows, _, supplier = source_rows(batch, user)
                enrich_rows(batch, rows, supplier)
                pairs = prepare_batch(batch, rows, supplier, user)
                for row, card in pairs:
                    if fetch_photos and not fetched and not card.images.filter(active=True).exists() and 'images' not in card.manual_fields:
                        # One bounded remote operation per consumer pass.
                        attempt = card.source.get('photo_attempt', {})
                        old = True
                        if attempt.get('at'):
                            try:
                                old = timezone.now() - timezone.datetime.fromisoformat(attempt['at']) >= timedelta(hours=24)
                            except ValueError:
                                pass
                        if old or attempt.get('url') != card.photo_url:
                            if fetch_card_photo(row, card):
                                card.refresh_from_db()
                                record(card, user, batch['key'], row, 'kaspi_card_photo_auto')
                            fetched = True
                    if fetch_photos and not fetched:
                        from .kaspi_enrichment import due, enrich_card
                        if due(card):
                            enrich_card(row,card,user,batch['key'],batch=batch)
                            fetched=True
                    if fetch_photos and user.is_active:
                        from .kaspi_card_jobs import queue_job
                        from .models import KaspiCardJob
                        if not competitor_data(row.get('offer'))['fresh'] and row.get('offer') and row['offer'].kaspi_url:
                            if not KaspiCardJob.objects.filter(card=card,user=user,kind='refresh').exists():
                                queue_job(card,user,'refresh',dict(batch=batch['key'],line=row['index']))
                    count += 1
        return count
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(88641233)')


def competitor_data(offer, days=7):
    observation = getattr(offer, 'kaspi_sellers', None) if offer else None
    valid = bool(observation and offer.kaspi_url and observation.source_url == offer.kaspi_url
        and observation.city == '750000000')
    sellers = observation.ranked_sellers if valid else []
    fresh = bool(valid and timezone.localdate() - timedelta(days=days) <= observation.checked_on <= timezone.localdate())
    return dict(snapshot=observation if valid else None, sellers=sellers, fresh=fresh,
        usable=bool(fresh and observation.complete and offer.match_verified and sellers))


def pricing(user, card, row, competitors, settings=None):
    if settings is None:
        rule = KaspiPricingRule.objects.filter(user=user).first()
        preference = Preferences.objects.filter(user=user).first()
        choice = KaspiCardPrice.objects.filter(user=user, card=card).first() if card.pk else None
    else:
        rule, preference, choices = settings
        choice = choices.get(card.pk)
    rule = rule or KaspiPricingRule(user=user, value=Decimal("3"))
    result = dict(rule=rule, choice=choice, mode=choice.mode if choice else 'algorithm',
        recommended=None, amount=None, profit=None, commission=None, expenses=None, explanation='', minimum=None)
    if preference and preference.commission is not None:
        result['commission'] = preference.commission
    if preference and preference.expenses is not None:
        result['expenses'] = max(Decimal('250'), preference.expenses)
    try:
        cost = Decimal(str(row['cost']))
    except (InvalidOperation, TypeError):
        cost = None
    rate = result['commission']
    expenses = result['expenses']
    if rule and rule.enabled and cost is not None and cost > 0 and rate is not None and Decimal(0) <= rate < Decimal(100) and expenses is not None:
        floor = ((cost + expenses + rule.min_profit) / (1 - rate / 100)).quantize(Decimal('1'), rounding=ROUND_CEILING)
        result['minimum'] = floor
        target = None
        if rule.method == 'cost_plus_percent':
            target = cost * (1 + rule.value / 100)
        elif competitors['usable']:
            low = min(Decimal(str(seller['price'])) for seller in competitors['sellers'])
            target = low - rule.value if rule.method == 'minimum_minus_amount' else low * (1 - rule.value / 100)
        if target is not None:
            result['recommended'] = max(floor, target.quantize(Decimal('1'), rounding=ROUND_CEILING))
            unit = '₸' if rule.method == 'minimum_minus_amount' else '%'
            result['explanation'] = (f'{rule.get_method_display()}: {rule.value} {unit}. '
                f'Расчётная цена: {target.quantize(Decimal("1"), rounding=ROUND_CEILING)} ₸. '
                f'Минимальная цена с учётом прибыли: {floor} ₸. '
                f'Итоговая рекомендация: {result["recommended"]} ₸.'
                + (' Цена повышена до границы минимальной прибыли.' if target < floor else ''))
        else:
            result['explanation'] = ('Не подтверждено точное совпадение товара Kaspi.' if row.get('offer') and not row['offer'].match_verified else 'Цены конкурентов устарели или список неполный. Нажмите «Обновить».')
    else:
        result['explanation'] = 'Алгоритм выключен либо не указаны комиссия, расходы или закупочная цена. Откройте настройки расчёта.'
    result['amount'] = choice.amount if choice and choice.mode == 'manual' else result['recommended']
    if result['amount'] is not None and cost is not None and rate is not None and expenses is not None:
        result['profit'] = result['amount'] * (1 - rate / 100) - cost - expenses
    return result


def readiness(card, images, price):
    from .kaspi_card_jobs import automatic_verified
    checks = [
        ('Фотография от 500 × 500', any(a.width >= 500 and a.height >= 500 for a in images)),
        ('Название и описание', bool(card.title and card.description)),
        ('Категория и характеристики', bool(card.category.startswith('Master -') and card.characteristics)),
        ('Применимость с источником', bool(card.applicability and card.applicability_source)),
        ('Проверены требования категории и комплектность', card.verified or automatic_verified(card)),
        ('Наша цена', price['amount'] is not None and price['amount'] > 0),
    ]
    return checks, all(ok for _, ok in checks)
