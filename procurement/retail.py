"""Owner-authorized Omega → AG Parts pilot. Procurement inputs stay private."""
import hashlib
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from urllib.parse import urlsplit

import requests
from PIL import Image
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from catalog.models import Product, SellerProfile, Category, ProductImage
from .models import Offer, RetailListing
from .importing import load_rows, money


def price_for(offer, pack):
    cost = offer.cost * pack
    price = (cost * Decimal('1.30')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    # A URL or an unverified candidate is not evidence of an identical item.
    if offer.match_verified and offer.kaspi_price is not None:
        snapshot = getattr(offer, 'kaspi_sellers', None)
        if snapshot and snapshot.complete and timedelta(0) <= timezone.localdate()-snapshot.checked_on <= timedelta(days=7):
            prices = [Decimal(str(s['price'])) for s in snapshot.sellers if Decimal(str(s['price'])) > 0]
            if prices:
                price = min(price, (min(prices)*pack).quantize(Decimal('1'), rounding=ROUND_FLOOR))
    reason = '' if price > cost + Decimal(250)*pack else 'Наценка не покрывает упаковку 250 ₸ на единицу. Требуется решение менеджера.'
    return int(price), reason


def stock_quantity(value):
    match = re.match(r'^\s*[>≥]?\s*(\d+)(?:\s|$)', value or '')
    return int(match[1]) if match else None


def refresh_listing(listing):
    offer = listing.offer
    product = listing.product
    pack = max(1, offer.min_order)
    listing.pack_size = pack
    price, reason = price_for(offer, pack)
    qty = stock_quantity(offer.stock)
    if qty is None:
        reason = reason or 'Остаток требует уточнения.'
    if not offer.checked_on or not timedelta(0) <= timezone.localdate()-offer.checked_on <= timedelta(days=7):
        reason = reason or 'Цена и наличие требуют обновления.'
    listing.hold_reason = reason
    content = listing.source_content
    title = content['title']
    if pack > 1:
        title += f' — упаковка {pack} шт.'
    product.title = title[:255]
    product.price = price
    product.cost_price = int((offer.cost*pack).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    product.stock_qty = qty//pack if qty is not None else 0
    product.price_on_request = bool(reason)
    product.status = 'active' if listing.enabled else 'hidden'
    product.description = content['description'] + f'\n\nПроизводитель: {offer.brand}. Артикул: {offer.article}.\nКоличество в цене: {pack} шт. Цена за упаковку.\nНаличие у поставщика подтверждаем перед оплатой. Доставка и самовывоз — по условиям AG Parts.'
    product.compatibility = content.get('compatibility','')
    product.oem_cross_references = content.get('oem','')
    product.save()
    listing.save(update_fields=['pack_size','hold_reason','updated_at'])
    return listing


def validate_image_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'omega-auto-parts.kz' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/content/files/images/products_images/[A-Za-z0-9_.%() -]+\.(jpg|jpeg|png|webp)',parsed.path,re.I)):
        raise ValueError('Фото должно быть прямой HTTPS-ссылкой на изображение товара Omega.')


def download_image(url):
    validate_image_url(url)
    with requests.get(url,timeout=(4,8),stream=True,allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Фото недоступно.')
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data)>10*1024*1024: raise ValueError('Фото превышает 10 МБ.')
    image = Image.open(io.BytesIO(data))
    if image.width*image.height>20000000: raise ValueError('Слишком большое изображение.')
    image.load()
    output=io.BytesIO();image.convert('RGB').save(output,'JPEG',quality=90)
    return output.getvalue()


def validate_publication(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=10: raise ValueError('Публикация: от 1 до 10 товаров.')
    if len({r.get('offer_id') for r in rows})!=len(rows): raise ValueError('Повтор товара.')
    for row in rows:
        offer=Offer.objects.get(pk=row['offer_id'],supplier__name='Omega')
        if row.get('article')!=offer.article or row.get('brand')!=offer.brand: raise ValueError('Артикул или бренд не совпадает.')
        for field in ('title','description','compatibility','oem'):
            if not isinstance(row.get(field,''),str) or len(row.get(field,''))>20000: raise ValueError('Некорректные данные карточки.')
        if not row.get('title') or not row.get('description'): raise ValueError('Нужны название и описание.')
        if not row.get('images') or len(row['images'])>3: raise ValueError('Нужны 1–3 фотографии.')
        for url in row['images']:validate_image_url(url)
        if not isinstance(row.get('checked_on'),str): raise ValueError('Нужна дата проверки.')
        checked=date.fromisoformat(row['checked_on'])
        if not timedelta(0)<=timezone.localdate()-checked<=timedelta(days=7):raise ValueError('Проверка источника устарела.')
        if row.get('source_url')!=offer.supplier_url:raise ValueError('Ссылка источника не совпадает.')
        money(row['cost'])
        if stock_quantity(row.get('stock')) is None:raise ValueError('Нужен прочитанный остаток.')
    return rows


def publish_one(row,user):
    validate_publication([row])
    # Download before acquiring database locks. Existing photos are preserved on updates.
    existing=RetailListing.objects.filter(offer_id=row['offer_id']).first()
    photos=[] if existing else [download_image(url) for url in row['images']]
    with transaction.atomic():
        offer=Offer.objects.select_for_update().get(pk=row['offer_id'],supplier__name='Omega')
        seller=SellerProfile.objects.get(slug='ag-parts')
        listing=RetailListing.objects.filter(offer=offer).select_related('product').first()
        if listing is None:
            if Product.objects.filter(seller_profile=seller,article=offer.article).exists():
                raise ValueError('У AG Parts уже есть этот артикул. Нужна ручная сверка, существующую карточку не меняем.')
            product=Product.objects.create(title=row['title'][:255],article=offer.article,seller_profile=seller,
                seller_name=seller.name,whatsapp_number=seller.phone,city=seller.city or 'Алматы',supplier='omega',status='hidden',condition='new')
            listing=RetailListing.objects.create(offer=offer,product=product,source_content=row,enabled=True)
            product.main_image.save(f'omega-{offer.pk}-main.jpg',ContentFile(photos[0]),save=True)
            for i,data in enumerate(photos[1:],1):
                gallery=ProductImage(product=product,sort_order=i)
                gallery.image.save(f'omega-{offer.pk}-{i}.jpg',ContentFile(data),save=True)
        else:
            listing.source_content=row
            listing.save(update_fields=['source_content','updated_at'])
        offer.cost=money(row['cost']);offer.stock=row['stock'];offer.min_order=int(row['min_order']);offer.checked_on=date.fromisoformat(row['checked_on'])
        offer.full_clean();offer.save()
        category,_=Category.objects.get_or_create(name=offer.category[:100] or 'Запчасти')
        listing.product.category=category
        listing.product.save(update_fields=['category'])
        refresh_listing(listing)
        # Copy only sourced make/model records. Manufacturer is not a vehicle make.
        from catalog.models import Brand,CarModel,Country,ProductFitment
        country,_=Country.objects.get_or_create(name='Прочее')
        for fit in offer.fitments.filter(verified=True):
            brand=Brand.objects.filter(name__iexact=fit.make).first()
            if brand is None:brand=Brand.objects.create(name=fit.make[:100],country=country)
            model,_=CarModel.objects.get_or_create(brand=brand,name=fit.model[:100])
            listing.product.selected_brands.add(brand);listing.product.selected_models.add(model)
            ProductFitment.objects.get_or_create(product=listing.product,car_model=model,source_url=fit.source_url,
                defaults={'status':'confirmed','engine_policy':'unknown','verified_on':date.fromisoformat(row['checked_on']),'note':'Модель из каталога поставщика. Перед заказом сверить VIN и модификацию.'})
        from .models import Activity
        Activity.objects.create(user=user,action='retail_publish',details={'offer':offer.pk,'product':listing.product_id,'hold':listing.hold_reason})
        return listing


def fingerprint(offer,listing):
    return hashlib.sha256(json.dumps([str(offer.cost),offer.stock,offer.min_order,str(offer.checked_on),str(listing.updated_at)],ensure_ascii=False).encode()).hexdigest()


def prepare_updates(file,checked_on):
    if checked_on>timezone.localdate():raise ValueError('Дата не может быть в будущем.')
    # Accept the original supplier workbook as well as the compact correction sheet.
    rows=load_rows(file,required={'article','brand'})
    result=[];seen=set()
    for number,row in rows:
        brand=str(row.get('brand') or '').strip();article=str(row.get('article') or '').strip()
        key=(brand,article)
        if key in seen:raise ValueError(f'Строка {number}: повтор бренда и артикула.')
        seen.add(key)
        listing=RetailListing.objects.select_related('offer','product').filter(offer__supplier__name='Omega',offer__brand=brand,offer__article=article).first()
        if listing is None:continue  # Full supplier files must not publish the remaining catalogue.
        offer=listing.offer
        if offer.checked_on and checked_on<offer.checked_on:raise ValueError(f'Строка {number}: файл старее действующих данных.')
        changes={}
        if row.get('cost') not in (None,''):changes['cost']=str(money(row['cost']))
        if row.get('stock') not in (None,''):
            stock=str(row['stock']).strip()
            if stock.endswith('>'):stock='>'+stock[:-1]
            if stock_quantity(stock) is None:raise ValueError(f'Строка {number}: остаток должен быть количеством.')
            changes['stock']=stock
        if row.get('active') not in (None,''):
            value=str(row['active']).strip().lower()
            if value not in ('1','0','да','нет','true','false'):raise ValueError('active: 1 или 0.')
            changes['active']=value in ('1','да','true')
        if not changes:raise ValueError(f'Строка {number}: нет изменений.')
        result.append({'offer_id':offer.pk,'brand':brand,'article':article,'before_cost':str(offer.cost),'before_stock':offer.stock,
            'changes':changes,'fingerprint':fingerprint(offer,listing),'checked_on':checked_on.isoformat()})
    if not result:raise ValueError('В файле нет опубликованных товаров Omega / AG Parts.')
    return result


@transaction.atomic
def apply_updates(rows,user):
    from .models import Activity
    for row in rows:
        offer=Offer.objects.select_for_update().get(pk=row['offer_id'],supplier__name='Omega')
        listing=RetailListing.objects.select_for_update().select_related('product').get(offer=offer)
        if fingerprint(offer,listing)!=row['fingerprint']:raise ValueError('Данные изменились после предпросмотра. Загрузите файл заново.')
        for field in ('cost','stock'):
            if field in row['changes']:setattr(offer,field,money(row['changes'][field]) if field=='cost' else row['changes'][field])
        offer.checked_on=date.fromisoformat(row['checked_on']);offer.full_clean();offer.save()
        if 'active' in row['changes']:listing.enabled=row['changes']['active'];listing.save(update_fields=['enabled'])
        refresh_listing(listing)
    Activity.objects.create(user=user,action='retail_excel',details={'rows':len(rows),'changes':rows})
    return len(rows)
