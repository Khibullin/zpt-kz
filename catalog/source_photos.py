"""Bounded, owner-staged public supplier-photo import; never changes prices/stock."""
import io
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from PIL import Image, ImageOps
from django.conf import settings
from django.db import transaction
from django.db.models import Case,IntegerField,Value,When
from django.core.files.base import ContentFile
from django.utils import timezone
from .models import Product, SupplierPhotoTask

MAX_BYTES=2*1024*1024
# The single consumer reuses HTTPS connections instead of negotiating TLS for
# every page and photograph. Redirects, bounds and source-stop rules stay intact.
_source_session=requests.Session()
_last_source_request=0.0


def optimized_images(data):
    """Keep the entire part and aspect ratio; never enlarge source pixels."""
    with Image.open(io.BytesIO(data)) as source:
        if source.format not in ('JPEG', 'PNG', 'WEBP') or source.width*source.height>20_000_000:
            raise ValueError('Неподходящий формат или размер изображения.')
        picture=ImageOps.exif_transpose(source)
        picture.thumbnail((800,800),Image.Resampling.LANCZOS)
        picture=picture.convert('RGBA')
        background=Image.new('RGBA',picture.size,'white')
        background.alpha_composite(picture)
        picture=background.convert('RGB')
    result=[]
    for edge,quality in ((800,80),(180,72)):
        resized=picture.copy()
        resized.thumbnail((edge,edge),Image.Resampling.LANCZOS)
        output=io.BytesIO()
        resized.save(output,'WEBP',quality=quality,method=6)
        result.append((output.getvalue(),resized.size))
    return result


def save_shared_image(storage,data):
    """Content addressing reuses identical encoded images, including after retry."""
    name='products/optimized/'+hashlib.sha256(data).hexdigest()+'.webp'
    if not storage.exists(name):
        saved=storage.save(name,ContentFile(data))
        if saved!=name and storage.exists(name):
            storage.delete(saved)
        else:
            name=saved
    return name

class SourceUnavailable(ValueError):
    pass


def public_page_url(url,brand=''):
    parsed=urlsplit(url)
    from .manufacturer_photos import valid_page
    if valid_page(url,brand):return url
    if parsed.scheme!='https' or parsed.netloc!='omega-auto-parts.kz' or parsed.query or parsed.fragment or not parsed.path.startswith('/'):
        raise ValueError('Недопустимый адрес поставщика.')
    return url


def matching_image(html,brand,article):
    """Only the matching JSON-LD Product; never images of recommended products."""
    def products(data):
        if isinstance(data,list):
            for item in data:yield from products(item)
        elif isinstance(data,dict):
            kind=data.get('@type')
            if kind=='Product' or isinstance(kind,list) and 'Product' in kind:yield data
            if '@graph' in data:yield from products(data['@graph'])
    for raw in re.findall(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',html,re.I|re.S):
        try:data=json.loads(raw)
        except (ValueError,TypeError):continue
        for product in products(data):
            maker=product.get('brand',{})
            maker=maker.get('name','') if isinstance(maker,dict) else maker
            sku=str(product.get('sku','')).strip().upper()
            expected=article.strip().upper()
            if not expected or not sku:continue
            exact=sku==expected
            # Some supplier pages put AF69006 in sku and AF-69006 in name.
            # Accept formatting only when the SAME page also states the exact
            # original article as a whole token. Never drop letters or suffixes.
            if not exact and re.sub(r'[-\s]','',sku)==re.sub(r'[-\s]','',expected):
                exact=bool(re.search(r'(?<!\w)'+re.escape(expected)+r'(?!\w)',str(product.get('name','')).upper()))
            if not exact or str(maker).strip().casefold()!=brand.strip().casefold():continue
            images=product.get('image',[])
            if isinstance(images,str):images=[images]
            for url in images if isinstance(images,list) else []:
                if not isinstance(url,str):continue
                from procurement.retail import validate_image_url
                try:validate_image_url(url)
                except ValueError:continue
                if Path(urlsplit(url).path).stem.casefold() in ('spg','no_image','no-image','placeholder','default'):continue
                return url
    return ''


def fetch_bytes(url,maximum=MAX_BYTES):
    global _last_source_request
    pause=.5-(time.monotonic()-_last_source_request)
    if pause>0:time.sleep(pause)
    _last_source_request=time.monotonic()
    with _source_session.get(url,timeout=(4,8),stream=True,allow_redirects=False) as response:
        if response.status_code in (403,429,503):
            raise SourceUnavailable(f'Поставщик недоступен: HTTP {response.status_code}; обработка остановлена без обхода ограничений.')
        response.raise_for_status()
        if response.status_code!=200:raise ValueError('Перенаправление или пустой источник не принимается.')
        data=bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data)>maximum:raise ValueError('Файл превышает установленный размер.')
    return bytes(data)


def process_photo_tasks(limit=10):
    blocked=list(SupplierPhotoTask.objects.filter(status='blocked').values('source_url','note','evidence'))
    if any(row['evidence'].get('block_kind')=='storage' or row['note'].startswith('Недостаточно свободного места') for row in blocked):return 0
    # Prepared work is activated by the deployed consumer, not the old process
    # while Render runs schema/data migrations for the next release.
    SupplierPhotoTask.objects.filter(status__in=['prepared','maker_ready','web_ready','bilstein_ready','shock_ready','weekend14h_ready']).update(status='pending')
    from .reviewed_remaining2 import process_reviewed_photos as process_remaining2_photos
    done=process_remaining2_photos(limit=limit)
    from .reviewed_zakaz100 import process_reviewed_photos as process_zakaz100_photos
    done+=process_zakaz100_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_cvvt import process_reviewed_photos as process_cvvt_photos
    done+=process_cvvt_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_bumpers4 import process_reviewed_photos as process_bumper_photos
    done+=process_bumper_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_next3 import process_reviewed_photos as process_next3_photos
    done+=process_next3_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_resume22 import process_reviewed_photos as process_resume22_photos
    done += process_resume22_photos(limit=max(0, limit - done))
    from .reviewed_zakaz_resume23 import process_reviewed_photos as process_resume23_photos
    done += process_resume23_photos(limit=max(0, limit - done))
    from .reviewed_zakaz_resume24 import process_reviewed_photos as process_resume24_photos
    done += process_resume24_photos(limit=max(0, limit - done))
    from .reviewed_zakaz_resume25 import process_reviewed_photos as process_resume25_photos
    done += process_resume25_photos(limit=max(0, limit - done))
    from .reviewed_new12d import process_reviewed_photos as process_new12d_photos
    done += process_new12d_photos(limit=max(0, limit - done))
    from .reviewed_new12a import process_reviewed_photos as process_new12a_photos
    done += process_new12a_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume9 import process_reviewed_photos as process_phresume9_photos
    done += process_phresume9_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume8 import process_reviewed_photos as process_phresume8_photos
    done += process_phresume8_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume7 import process_reviewed_photos as process_phresume7_photos
    done += process_phresume7_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume6 import process_reviewed_photos as process_phresume6_photos
    done += process_phresume6_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume5 import process_reviewed_photos as process_phresume5_photos
    done += process_phresume5_photos(limit=max(0, limit - done))
    from .reviewed_zakaz_resume26 import process_reviewed_photos as process_resume26_photos
    done += process_resume26_photos(limit=max(0, limit - done))
    from .reviewed_nightly_20261114_d import process_reviewed_photos as process_nightly14d_photos
    done += process_nightly14d_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume4 import process_reviewed_photos as process_phresume4_photos
    done += process_phresume4_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume3 import process_reviewed_photos as process_phresume3_photos
    done += process_phresume3_photos(limit=max(0, limit - done))
    from .reviewed_phaeton_resume2 import process_reviewed_photos as process_phresume2_photos
    done+=process_phresume2_photos(limit=max(0,limit-done))
    from .reviewed_phaeton_resume1 import process_reviewed_photos as process_phresume1_photos
    done+=process_phresume1_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_resume21 import process_reviewed_photos as process_resume21_photos
    done+=process_resume21_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_resume20 import process_reviewed_photos as process_resume20_photos
    done+=process_resume20_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_resume8 import process_reviewed_photos as process_resume8_photos
    done+=process_resume8_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_resume4 import process_reviewed_photos as process_resume4_photos
    done+=process_resume4_photos(limit=max(0,limit-done))
    from .reviewed_zakaz_more7 import process_reviewed_photos as process_more7_photos
    done+=process_more7_photos(limit=max(0,limit-done))
    from .reviewed_top50 import process_reviewed_photos
    done+=process_reviewed_photos(limit=max(0,limit-done))
    from .reviewed_next50 import process_reviewed_photos as process_next50_photos
    done+=process_next50_photos(limit=max(0,limit-done))
    from .reviewed_next50_extra import process_reviewed_photos as process_extra_photos
    done+=process_extra_photos(limit=max(0,limit-done))
    pending=SupplierPhotoTask.objects.filter(status='pending')
    for host in {urlsplit(row['source_url']).netloc for row in blocked}:
        if host:pending=pending.exclude(source_url__startswith='https://'+host+'/')
    pending=pending.annotate(source_priority=Case(When(evidence__source_probe=True,then=Value(-1)),When(source_url__startswith='https://omega-auto-parts.kz/',then=Value(1)),default=Value(0),output_field=IntegerField()))
    for task_id in list(pending.order_by('source_priority','pk').values_list('pk',flat=True)[:max(0,limit-done)]):
        with transaction.atomic():
            task=SupplierPhotoTask.objects.select_for_update(skip_locked=True,of=('self',)).select_related('product','offer').filter(pk=task_id,status='pending').first()
            if task is None:continue
            try:
                product=task.product;offer=task.offer
                if product.main_image:
                    task.status='skipped';task.note='Существующая фотография сохранена.'
                elif product.status!='active' or product.article.strip().upper()!=offer.article.strip().upper():
                    task.status='skipped';task.note='Карточка изменилась; автоматическое добавление отменено.'
                else:
                    from procurement.models import RetailListing
                    listing=RetailListing.objects.select_related('offer').filter(product=product,enabled=True).first()
                    if not listing or listing.offer.brand.strip().casefold()!=offer.brand.strip().casefold():
                        raise ValueError('Не подтверждено совпадение производителя.')
                    html=fetch_bytes(public_page_url(task.source_url,offer.brand)).decode('utf-8',errors='replace')
                    if urlsplit(task.source_url).netloc=='omega-auto-parts.kz':
                        image_url=matching_image(html,offer.brand,offer.article)
                    else:
                        from .manufacturer_photos import matching_manufacturer_image
                        image_url=matching_manufacturer_image(html,task.source_url,offer.brand,offer.article)
                    if not image_url:
                        task.status='skipped';task.note='Точное совпадение или реальная фотография не найдены.'
                    else:
                        data=fetch_bytes(image_url)
                        (main,main_size),(thumb,thumb_size)=optimized_images(data)
                        media=Path(settings.MEDIA_ROOT);media.mkdir(parents=True,exist_ok=True)
                        if shutil.disk_usage(media).free<len(main)+len(thumb)+64*1024*1024:
                            task.status='blocked';task.note='Недостаточно свободного места; тариф не изменён.'
                            task.evidence={**task.evidence,'block_kind':'storage','free_disk_bytes':shutil.disk_usage(media).free}
                        else:
                            # Re-read immediately before saving; do not replace an image
                            # a manager added while the external source was loading.
                            product=Product.objects.select_for_update().get(pk=product.pk)
                            if product.main_image or product.status!='active' or product.article.strip().upper()!=offer.article.strip().upper():
                                task.status='skipped';task.note='Карточка обновлена менеджером; фото сохранено без изменений.'
                            else:
                                product.main_image.name=save_shared_image(product.main_image.storage,main)
                                product.supplier_thumbnail.name=save_shared_image(product.supplier_thumbnail.storage,thumb)
                                product.updated_at=timezone.now()
                                product.save(update_fields=['main_image','supplier_thumbnail','updated_at'])
                                type(offer).objects.filter(pk=offer.pk,photo_url='').update(photo_url=image_url)
                                task.status='done';task.note='Фотография точного товара из подтверждённого открытого каталога.'
                                task.evidence={**task.evidence,'source_url':task.source_url,'image_url':image_url,'brand':offer.brand,'article':offer.article,'source_bytes':len(data),'bytes':len(main)+len(thumb),'main_bytes':len(main),'thumbnail_bytes':len(thumb),'main_size':main_size,'thumbnail_size':thumb_size,'main_path':product.main_image.name,'thumbnail_path':product.supplier_thumbnail.name,'free_disk_bytes':shutil.disk_usage(media).free,'checked_at':timezone.now().isoformat()}
            except SourceUnavailable as error:
                task.status='blocked';task.note=str(error)[:400]
                task.evidence={**task.evidence,'block_kind':'source'}
            except (requests.RequestException,ValueError,OSError,SyntaxError,Image.DecompressionBombError) as error:
                task.status='skipped';task.note=str(error)[:400]
            task.save(update_fields=['status','note','evidence','updated_at']);done+=1
            if task.status=='blocked':break
        time.sleep(.3)
    return done


