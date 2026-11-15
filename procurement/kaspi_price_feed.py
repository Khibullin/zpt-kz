"""Merge approved new offers into the existing full shop feed, in the consumer."""
from datetime import timedelta
from decimal import Decimal,ROUND_CEILING
from xml.etree import ElementTree as ET
from django.conf import settings
from django.core import signing
from django.http import Http404,HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from .kaspi_catalog_api import connected
from .models import KaspiPriceFeed,KaspiCardJob


def feed_configured():
    return all(getattr(settings,key,'') for key in ('KASPI_MERCHANT_ID','KASPI_POINT_OF_SERVICE','KASPI_BASELINE_PRICE_FEED_URL'))


def integration_ready():
    return connected() and feed_configured() and getattr(settings,'KASPI_PRICE_FEED_CONNECTED',False)


def feed_url():
    if not feed_configured(): return ''
    token=signing.Signer(salt='kaspi-full-price-feed').sign(settings.KASPI_MERCHANT_ID)
    return str(getattr(settings,'PUBLIC_BASE_URL','') or 'https://zpt.kz').rstrip('/')+reverse('procurement:kaspi_price_feed',args=[token])


def merge_feed(original,rows,merchant,store):
    root=ET.fromstring(original)
    if root.tag!='{kaspiShopping}kaspi_catalog' or root.findtext('{kaspiShopping}merchantid')!=merchant:
        raise ValueError('Исходный XML не относится к подключённому магазину Kaspi.')
    offers=root.find('{kaspiShopping}offers')
    if offers is None: raise ValueError('Нет списка товаров в исходном XML.')
    ET.register_namespace('','kaspiShopping')
    def tag(name): return '{kaspiShopping}'+name
    existing={offer.get('sku'):offer for offer in offers}
    for row in rows:
        sku=row['sku']
        if len(sku)>20: raise ValueError('Артикул магазина длиннее 20 символов.')
        if sku in existing: offers.remove(existing[sku])
        offer=ET.SubElement(offers,tag('offer'),sku=sku)
        existing[sku]=offer
        ET.SubElement(offer,tag('model')).text=row['title']
        ET.SubElement(offer,tag('brand')).text=row['brand']
        available=ET.SubElement(offer,tag('availabilities'))
        try: qty=Decimal(str(row['quantity']))
        except (ArithmeticError,TypeError): raise ValueError('Не подтверждён складской остаток.')
        if not qty.is_finite() or qty<0 or qty!=qty.to_integral_value(): raise ValueError('Не подтверждён целочисленный складской остаток.')
        ET.SubElement(available,tag('availability'),available='yes' if qty>0 else 'no',storeId=store,stockCount=str(int(qty)))
        amount=Decimal(str(row['price'])).quantize(Decimal('1'),rounding=ROUND_CEILING)
        if amount<=0: raise ValueError('Не рассчитана цена для прайс-листа.')
        ET.SubElement(offer,tag('price')).text=str(amount)
    root.set('date',timezone.now().isoformat())
    return ET.tostring(root,encoding='utf-8',xml_declaration=True).decode()


def refresh_price_feed():
    if not feed_configured(): return False
    now=timezone.now()
    feed,_=KaspiPriceFeed.objects.get_or_create(pk='main')
    if feed.attempted_at and now-feed.attempted_at<timedelta(minutes=1): return False
    feed.attempted_at=now;feed.save(update_fields=['attempted_at'])
    try:
        from .kaspi_enrichment import fetch_page
        if settings.KASPI_BASELINE_PRICE_FEED_URL==feed_url(): raise ValueError('Исходный прайс-лист не может ссылаться на результат объединения.')
        original=fetch_page(settings.KASPI_BASELINE_PRICE_FEED_URL)
        from .kaspi_workspace import purchased_batches,source_rows,enrich_rows
        from .kaspi_materials import pricing,competitor_data,identity_for
        rows=[]
        batches={}
        for job in KaspiCardJob.objects.filter(kind='publish',status__in=['done','awaiting_listing','published']).select_related('card','user'):
            if job.payload.get('merchant')!=settings.KASPI_MERCHANT_ID: raise ValueError('Магазин опубликованного товара изменился.')
            key=(job.user_id,job.payload['batch'])
            if key not in batches:
                batch=next((b for b in purchased_batches(job.user) if b['key']==job.payload['batch']),None)
                if not batch: raise ValueError('Нет доступа к закупке опубликованного товара.')
                source,_,supplier=source_rows(batch,job.user);enrich_rows(batch,source,supplier,compact=True)
                batches[key]=(batch,source,supplier)
            batch,source,supplier=batches[key]
            row=next((row for row in source if row['index']==job.payload['line']),None)
            if not row or identity_for(batch,row,supplier)!=job.card.identity: raise ValueError('Опубликованный товар изменён в закупке.')
            if getattr(batch['obj'],'warehouse',None)!=settings.KASPI_LOCAL_WAREHOUSE or row.get('stock') is None:
                raise ValueError('Для опубликованного товара не подтверждено соответствие склада и фактический остаток.')
            price=pricing(job.user,job.card,row,competitor_data(row.get('offer')))['amount']
            if price is None: raise ValueError('Цена опубликованного товара требует обновления конкурентов или ручного ввода.')
            rows.append(dict(sku=job.payload['material']['sku'],title=job.card.title,brand=job.card.brand,quantity=row['stock'],price=price))
            result={**job.result,'feed_price':str(price)}
            changes={'result':result}
            if job.status=='published' and job.result.get('feed_price')!=str(price):
                changes.update(status='awaiting_listing',reason='Цена изменена. Ожидается подтверждение новой цены в Kaspi.',updated_at=now)
            KaspiCardJob.objects.filter(pk=job.pk).update(**changes)
        feed.xml=merge_feed(original,rows,settings.KASPI_MERCHANT_ID,settings.KASPI_POINT_OF_SERVICE)
        feed.error='';feed.built_at=now
    except Exception as exc:
        feed.error=str(exc)[:500] if isinstance(exc,ValueError) else 'Не удалось обновить полный прайс-лист. Прежний результат сохранён.'
    feed.save()
    return True


@require_GET
def price_feed(request,token):
    try:
        merchant=signing.Signer(salt='kaspi-full-price-feed').unsign(token)
        if not feed_configured() or merchant!=settings.KASPI_MERCHANT_ID: raise ValueError
    except (signing.BadSignature,ValueError): raise Http404
    feed=KaspiPriceFeed.objects.filter(pk='main').first()
    if not feed or not feed.xml or feed.error or not feed.built_at or timezone.now()-feed.built_at>timedelta(minutes=5):
        return HttpResponse('Прайс-лист временно не подтверждён.',status=503)
    response=HttpResponse(feed.xml,content_type='application/xml; charset=utf-8')
    response['Cache-Control']='private, no-store'
    return response
