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
    SupplierPhotoTask.objects.filter(status__in=['prepared','maker_ready','web_ready','bilstein_ready','shock_ready','weekend14h_ready','weekend14i_ready','weekend14j_ready','weekend14k_ready','weekend14n_ready','weekend15a_ready','weekend15b_ready','weekend15c_ready','weekend15d_ready','weekend15e_ready','weekend15f_ready','weekend15g_ready','weekend15h_ready','weekend15i_ready','weekend15j_ready','weekend15k_ready','weekend15l_ready','weekend15m_ready','weekend15n_ready','weekend15o_ready','weekend15p_ready','weekend15r_ready','weekend15s_ready','weekend15t_ready']).update(status='pending')
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
                    evidence=task.evidence or {}
                    exact_jashi=(
                        task.pk==4759 and product.pk==6786 and offer.pk==25082
                        and offer.brand=='JASHI' and offer.article=='C281F260202-0200'
                        and task.source_url=='https://kaspi.kz/shop/p/jashi-perednii-c281f260202-0200-1-sht-130050225/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-e'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p62/p7d/8356815.png?format=gallery-large'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_trialli=(
                        task.pk==4814 and product.pk==6841 and offer.pk==25170
                        and offer.brand=='TRIALLI' and offer.article=='AG 03007'
                        and task.source_url=='https://shop.globalparts.kz/catalog/card/184634'
                        and evidence.get('reviewed_batch')=='weekend-20261115-f'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/h6d/h92/67650614853662.jpg?format=gallery-large'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_hyundai_kia=(
                        task.pk==4686 and product.pk==6713 and offer.pk==25129
                        and offer.brand=='HYUNDAI-KIA' and offer.article=='553102P500'
                        and task.source_url=='https://partsouq.com/shop/product/553102P500-hyundai-kia-shock-absorber-assy/17998151'
                        and evidence.get('reviewed_batch')=='weekend-20261115-g'
                        and evidence.get('image_url')=='https://partsouq.com/assets/tesseract/assets/partsimages/Hyundai-/-KIA/553102P500.jpg'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_ssangyong=(
                        task.pk==4800 and product.pk==6827 and offer.pk==25081
                        and offer.brand=='SsangYong' and offer.article=='1612003114'
                        and task.source_url=='https://plenty.parts/parts/ssangyong/engine/ssangyong-1612003114-vibration-damper-for-ssangyong-kg-mobility-p2259592'
                        and evidence.get('reviewed_batch')=='weekend-20261115-h'
                        and evidence.get('image_url')=='https://i.plenty.parts/i/850/pictures_ppp_ssangyong_1612003114.jpg'
                        and evidence.get('verified_image_sha256')=='c4f312d7d6fedb6feb51b0bb1facebd3c1c307deb01a92fccdc7b010cff1391e'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_haval=(
                        task.pk==4881 and product.pk==6908 and offer.pk==25373
                        and offer.brand=='HAVAL' and offer.article=='2804201XKN01A'
                        and task.source_url=='https://www.shindary.com/bumper/rear-bumper-2804201xkn01a-for-haval-h6.html'
                        and evidence.get('reviewed_batch')=='weekend-20261115-i'
                        and evidence.get('image_url')=='https://www.shindary.com/data/upload/20240523/2804201XKN01A.jpg'
                        and evidence.get('verified_image_sha256')=='393d76bd812e5dcc9f086c8a4001cdfd2216bed2ce5990bb1a791f613fa92dab'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_jashi_54651=(
                        task.pk==4737 and product.pk==6764 and offer.pk==25213
                        and offer.brand=='JASHI' and offer.article=='54651-D3000'
                        and task.source_url=='https://shop.globalparts.kz/catalog/card/210534'
                        and evidence.get('reviewed_batch')=='weekend-20261115-j'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p65/pe2/68673997.png?format=gallery-large'
                        and evidence.get('verified_image_sha256')=='b75b65ea9254a36b432e5b51c7111eec80c5a4ee222d3120b8d2cf6074bfc9c9'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_jashi_54650=(
                        task.pk==4930 and product.pk==6957 and offer.pk==25289
                        and offer.brand=='JASHI' and offer.article=='54650H5100'
                        and task.source_url=='https://kaspi.kz/shop/p/jashi-perednii-54650h5100-1-sht-144183578/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-k'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p88/p76/58890227.png?format=gallery-medium'
                        and evidence.get('verified_image_sha256')=='cde6c7ba0589258b38fffe912858fecbc633341d7c0a19a408fad67c093f38e1'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_jashi_54660=(
                        task.pk==4934 and product.pk==6961 and offer.pk==25290
                        and offer.brand=='JASHI' and offer.article=='54660H5100'
                        and task.source_url=='https://kaspi.kz/shop/p/jashi-perednii-54660h5100-1-sht-144184316/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-l'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/ped/pc6/58893082.png?format=gallery-medium'
                        and evidence.get('verified_image_sha256')=='8bb584b6700964c3f43056682be114064b98a7958014f5630ee7a9aefd610e4e'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_fenox_a12254=(
                        task.pk==4671 and product.pk==6698 and offer.pk==25142
                        and offer.brand=='Fenox' and offer.article=='A12254'
                        and task.source_url=='https://armtek.by/product/amortizator-maslyanyy-fenox-a12254-zadnyaya-os-sleva-zadnyaya-os-sprava-4363063'
                        and evidence.get('reviewed_batch')=='weekend-20261115-m'
                        and evidence.get('image_url')=='https://img.armtek.ru/img/article/436/4363063/500x500/4363063_0.webp'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_jashi_step_set=(
                        task.pk==7210 and product.pk==9237 and offer.pk==28110
                        and offer.brand=='JASHI' and offer.article=='8405120P3011/SET/6p/'
                        and task.source_url=='https://kaspi.kz/shop/p/jashi-kreplenie-8405120p3011-jac-t6-120901371/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-n'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/hcb/h6e/86407480410142.jpg?format=gallery-large'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_hyundai_kia_55100=(
                        task.pk==4888 and product.pk==6915 and offer.pk==25352
                        and offer.brand=='HYUNDAI-KIA' and offer.article=='55100H5600'
                        and task.source_url=='https://kaspi.kz/shop/p/hyundai-kia-balka-zadnei-podveski-55100h5600-146288405/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-o'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p58/pa8/66804531.png?format=gallery-medium'
                        and evidence.get('verified_image_sha256')=='2504af577c30a4a0cb5670e642766acfe2566670e219ad6515c37e98c0fe710b'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_parts_mall_pqb007=(
                        task.pk==4786 and product.pk==6813 and offer.pk==25159
                        and offer.brand=='Parts-Mall' and offer.article=='pqb-007'
                        and task.source_url=='https://kaspi.kz/shop/p/pmc-parts-mall-pqb-007-upor-kapota-dlja-kia-sorento-2002-2005-chernyi-179355295/'
                        and evidence.get('reviewed_batch')=='weekend-20261115-p'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p1f/pa6/187024947.jpg?format=gallery-medium'
                        and evidence.get('verified_image_sha256')=='35d970accf69a516de64f1b77333d4139c844aa5e9da6e3b68e82504900a2fd6'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_ssangyong_7146034004=(
                        task.pk==4807 and product.pk==6834 and offer.pk==25086
                        and offer.brand=='SsangYong' and offer.article=='7146034004'
                        and task.source_url=='https://www.autoopt.ru/catalog/912908-amortizator_ssangyong_actyon_10__kryshki_bagazhnika_pravyj_oe'
                        and evidence.get('reviewed_batch')=='weekend-20261115-r'
                        and evidence.get('image_url')=='https://www.autoopt.ru/product_pictures/big/734/912908.jpg'
                        and evidence.get('verified_image_sha256')=='dc00b266a7e6a6aaa2ca502e861fc4d3441966588e80002de66172c917870a78'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_ssangyong_7145034004=(
                        task.pk==4805 and product.pk==6832 and offer.pk==25085
                        and offer.brand=='SsangYong' and offer.article=='7145034004'
                        and task.source_url=='https://www.autoopt.ru/catalog/912907-amortizator_ssangyong_actyon_10__kryshki_bagazhnika_levyj_oe'
                        and evidence.get('reviewed_batch')=='weekend-20261115-s'
                        and evidence.get('image_url')=='https://www.autoopt.ru/product_pictures/big/0fe/912907.jpg'
                        and evidence.get('verified_image_sha256')=='f3748cf1663c711d9ec25b01031a0400df5d3bf2614dbd5f5e460a9a060baf07'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    exact_hyundai_kia_553112s400=(
                        task.pk==4687 and product.pk==6714 and offer.pk==25131
                        and offer.brand=='HYUNDAI-KIA' and offer.article=='553112S400'
                        and task.source_url=='https://kaspi.kz/shop/p/hyundai-kia-zadnii-553112s400-1-sht-142570155/?c=750000000'
                        and evidence.get('reviewed_batch')=='weekend-20261115-t'
                        and evidence.get('image_url')=='https://resources.cdn-kaspi.kz/img/m/p/p9b/p96/53188476.png?format=gallery-medium'
                        and evidence.get('checked_on')=='2026-11-15'
                    )
                    if exact_jashi or exact_trialli or exact_hyundai_kia or exact_ssangyong or exact_haval or exact_jashi_54651 or exact_jashi_54650 or exact_jashi_54660 or exact_fenox_a12254 or exact_jashi_step_set or exact_hyundai_kia_55100 or exact_parts_mall_pqb007 or exact_ssangyong_7146034004 or exact_ssangyong_7145034004 or exact_hyundai_kia_553112s400:
                        # The exact card and selected item image were visually checked before
                        # staging. Fetch only that immutable CDN object here; the guarded
                        # migration has already rechecked live identity, stock and fitment.
                        image_url=evidence['image_url']
                    else:
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


