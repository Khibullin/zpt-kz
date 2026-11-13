"""Durable, bounded jobs for explicitly requested Kaspi operations."""
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import KaspiCardJob, KaspiPreparedCard, KaspiSellerSnapshot, Offer, Activity
from .kaspi_catalog_api import CatalogClient, CatalogError, CatalogUncertain, connected, signed_image_url

LABELS={'pending':'В очереди','running':'Обновляется','submitted':'Отправлено в Kaspi · обработка',
    'done':'Завершено','awaiting_listing':'Ожидается размещение','published':'Опубликовано','error':'Ошибка','unknown':'Требуется проверить результат отправки'}


def api_attributes(card):
    saved=card.source.get('kaspi_schema',{})
    if saved.get('category')!=card.category:
        return [], ['Не получены требования выбранной категории Kaspi.']
    supplied={**saved.get('values',{}),**card.characteristics}
    normalized={str(k).strip().casefold():v for k,v in card.characteristics.items()}
    values=[]; missing=[]
    for spec in saved.get('attributes',[]):
        if not isinstance(spec,dict) or not spec.get('code'): continue
        code=spec['code']; name=spec.get('title') or spec.get('name') or code
        value=supplied.get(code)
        if str(name).strip().casefold() in normalized: value=normalized[str(name).strip().casefold()]
        if value is None or value=='':
            if spec.get('mandatory'): missing.append(str(name))
            continue
        items=value if isinstance(value,list) else [value]
        valid=True
        for item in items:
            kind=spec.get('type')
            if kind=='number':
                try: valid=valid and Decimal(str(item)).is_finite()
                except Exception: valid=False
            elif kind=='boolean': valid=valid and isinstance(item,bool)
            else: valid=valid and isinstance(item,str)
        if not spec.get('multiValued') and isinstance(value,list): valid=False
        if not valid:
            missing.append(str(name)+' — неверный тип значения'); continue
        values.append({'code':code,'value':value})
    if not saved.get('attributes'): missing.append('Не загружен список характеристик категории.')
    return values,missing


def automatic_verified(card):
    _,missing=api_attributes(card)
    return bool(card.source.get('kaspi_schema') and not missing)


def queue_job(card,user,kind,payload):
    with transaction.atomic():
        KaspiPreparedCard.objects.select_for_update().get(pk=card.pk)
        job=KaspiCardJob.objects.filter(card=card,kind=kind)
        if kind!='publish': job=job.filter(user=user)
        job=job.first()
        if job and (job.status in ('pending','running','submitted','unknown','awaiting_listing','published') or kind=='publish' and job.status=='done'):
            return job,False
        if job is None: job=KaspiCardJob(card=card,user=user,kind=kind)
        job.status='pending'; job.payload=payload; job.reason=''; job.result={}; job.external_code='';job.save()
        Activity.objects.create(user=user,action='kaspi_'+kind+'_queued',details={'card':card.pk,'kaspi_batch':payload['batch']})
    return job,True


def freeze_publication(card,user,row,batch,source_token):
    from .kaspi_materials import readiness, pricing, competitor_data
    images=list(card.images.filter(active=True))
    price=pricing(user,card,row,competitor_data(row.get('offer')))
    checks,ready=readiness(card,images,price)
    attributes,missing=api_attributes(card)
    if not ready or missing:
        raise ValueError('Нужно дополнить: '+', '.join([label for label,ok in checks if not ok]+missing))
    from .kaspi_price_feed import integration_ready
    if not integration_ready(): raise ValueError('Для автоматической публикации подключите API магазина и полный XML прайс-лист. Ручное размещение доступно.')
    if row.get('stock') is None: raise ValueError('Нет подтверждённого складского остатка для автоматической публикации.')
    from django.conf import settings
    return dict(merchant=settings.KASPI_MERCHANT_ID,batch=batch['key'],line=row['index'],revision=card.revision,source_token=source_token,
        material=dict(sku='ZPT-'+card.identity[:16],title=card.title,brand=card.brand,category=card.category,
            description=card.description,attributes=attributes),image_ids=[a.pk for a in images if min(a.width,a.height)>=500],
        price=str(price['amount']),quantity=str(row.get('stock') if row.get('stock') is not None else row['quantity']))


def process_card_jobs():
    """At most one network job per consumer pass; no remote call inside a transaction."""
    now=timezone.now()
    # A crashed POST must never be repeated automatically.
    KaspiCardJob.objects.filter(kind='publish',status='running',updated_at__lt=now-timedelta(minutes=5)).update(
        status='unknown',reason='Обработчик остановился во время отправки. Проверьте кабинет Kaspi; повтор заблокирован.',updated_at=now)
    KaspiCardJob.objects.filter(kind='refresh',status='running',updated_at__lt=now-timedelta(minutes=5)).update(status='pending',updated_at=now)
    job=KaspiCardJob.objects.filter(status__in=['pending','submitted','awaiting_listing']).exclude(
        status='submitted',updated_at__gt=now-timedelta(seconds=30)).exclude(
        status='awaiting_listing',updated_at__gt=now-timedelta(seconds=60)).select_related('card','card__offer','user').order_by('updated_at').first()
    if not job: return False
    if not KaspiCardJob.objects.filter(pk=job.pk,status=job.status,updated_at=job.updated_at).update(status='running',updated_at=now): return False
    from .permissions import workspace_role
    try:
        if not job.user.is_active or workspace_role(job.user) not in ('sales','admin'):
            raise ValueError('Права пользователя изменились. Операция остановлена.')
        from .kaspi_workspace import purchased_batches,source_rows,source_signature,enrich_rows
        from .kaspi_materials import identity_for
        batch=next((b for b in purchased_batches(job.user) if b['key']==job.payload['batch']),None)
        if not batch: raise ValueError('Закупка больше недоступна.')
        rows,_,supplier=source_rows(batch,job.user)
        row=next((r for r in rows if r['index']==job.payload['line']),None)
        if not row or identity_for(batch,row,supplier)!=job.card.identity:
            raise ValueError('Состав закупки изменился. Операция остановлена.')
        if job.kind=='refresh':
            from .kaspi_refresh import read_complete_sellers
            from django.core.cache import cache
            if cache.get('kaspi-card-refresh-paused'):
                raise ValueError('Источник Kaspi временно недоступен. Повторите обновление позже.')
            offer=job.card.offer
            if not offer or not offer.kaspi_url: raise ValueError('Не найдена карточка Kaspi точного товара. Автоподстановка похожей карточки запрещена.')
            original=(offer.brand,offer.article,offer.kaspi_url)
            try: sellers=read_complete_sellers(offer.kaspi_url)
            except Exception:
                cache.set('kaspi-card-refresh-paused',True,1800)
                raise ValueError('Источник Kaspi не подтвердил полный список продавцов. Прежние данные сохранены; обновление приостановлено на 30 минут.')
            with transaction.atomic():
                current=Offer.objects.select_for_update().get(pk=offer.pk)
                if (current.brand,current.article,current.kaspi_url)!=original: raise ValueError('Товар изменён во время проверки.')
                KaspiSellerSnapshot.objects.update_or_create(offer=current,defaults=dict(source_url=current.kaspi_url,
                    city='750000000',checked_on=timezone.localdate(),complete=True,sellers=sellers))
            job.status='done';job.reason='Продавцы и цены обновлены. Ручная цена сохранена.';job.result={'sellers':len(sellers)}
        else:
            from django.conf import settings
            if job.payload.get('merchant')!=settings.KASPI_MERCHANT_ID: raise ValueError('Подключённый магазин изменился. Отправка остановлена.')
            api=CatalogClient()
            if job.status=='awaiting_listing':
                from django.conf import settings
                from django.core.cache import cache
                from .kaspi_refresh import read_complete_sellers
                url=job.result.get('public_url') or (job.card.offer.kaspi_url if job.card.offer and job.card.offer.match_verified else '')
                if not url or cache.get('kaspi-card-refresh-paused'):
                    job.status='awaiting_listing';job.reason='Карточка принята. Ожидается подтверждение нашего предложения и цены в Kaspi.'
                else:
                    try: sellers=read_complete_sellers(url)
                    except Exception:
                        cache.set('kaspi-card-refresh-paused',True,1800)
                        raise ValueError('Не удалось проверить фактическое размещение. Карточка и прайс-лист сохранены.')
                    expected=Decimal(job.result.get('feed_price',job.payload['price']))
                    ours=next((seller for seller in sellers if str(seller.get('merchant_id'))==str(settings.KASPI_MERCHANT_ID) and Decimal(seller['price'])==expected),None)
                    job.status='published' if ours else 'awaiting_listing'
                    job.reason='Размещение и наша цена подтверждены в Kaspi.' if ours else 'Ожидается обновление нашего предложения и цены в Kaspi.'
                    if ours: job.result={**job.result,'public_url':url}
            elif job.external_code:
                status=api.status(job.external_code)
                if status.get('status')=='FINISHED':
                    result=api.result(job.external_code)
                    job.result=result
                    if not isinstance(result,dict) or 'errors' not in result or 'result' not in result:
                        raise CatalogError('Kaspi не вернул полный результат импорта. Проверка будет повторена без повторной отправки.')
                    item=result.get('result',{}).get(job.payload['material']['sku'])
                    if not result.get('errors') and (result.get('total')!=1 or not isinstance(item,dict) or item.get('state')!='FINISHED'):
                        raise CatalogError('Нет подтверждения приёма именно этой карточки. Проверка статуса будет повторена без повторной отправки.')
                    if result.get('errors'):
                        job.status='error'; job.reason='Kaspi отклонил карточку. Проверьте результат импорта в кабинете Kaspi.'
                    else:
                        job.status='awaiting_listing'; job.reason='Карточка принята Kaspi. Цена и остаток передаются через полный прайс-лист; размещение ещё не подтверждено.'
                        code=item.get('productCode') or item.get('productId')
                        if str(code or '').isdigit(): job.result['public_url']=f'https://kaspi.kz/shop/p/-{code}/?c=750000000'
                elif status.get('status') in ('FAILED','ERROR'):
                    job.status='error';job.reason='Kaspi сообщил об ошибке обработки карточки.'
                else: job.status='submitted';job.reason='Kaspi обрабатывает карточку.'
            else:
                from django.core import signing
                signed=signing.loads(job.payload['source_token'],salt='kaspi-card-source',max_age=86400)
                if signed!={'batch':batch['key'],'source':source_signature(batch,rows)} or job.card.revision!=job.payload['revision']:
                    raise ValueError('Карточка или закупка изменились после постановки в очередь. Откройте обновлённую карточку.')
                material=job.payload['material']
                images=list(job.card.images.filter(pk__in=job.payload['image_ids'],active=True))
                if len(images)!=len(job.payload['image_ids']): raise ValueError('Фотографии изменились. Обновите карточку.')
                material['images']=[{'url':signed_image_url(image)} for image in images]
                result=api.submit(material)
                job.external_code=str(result['code'])[:160];job.result=result;job.status='submitted';job.reason='Карточка отправлена в Kaspi; ожидается результат обработки.'
    except CatalogUncertain as exc:
        job.status='unknown';job.reason=str(exc)
    except Exception as exc:
        job.status=('awaiting_listing' if job.status=='awaiting_listing' else 'submitted') if job.external_code else 'error'
        job.reason=str(exc)[:1200] if isinstance(exc,ValueError) else 'Временная ошибка обработки. Сохранённые данные не изменены.'
    job.save(update_fields=['status','reason','result','external_code','updated_at'])
    Activity.objects.create(user=job.user,action='kaspi_'+job.kind+'_result',details={
        'card':job.card_id,'kaspi_batch':job.payload['batch'],'status':job.status,'reason':job.reason})
    return True
