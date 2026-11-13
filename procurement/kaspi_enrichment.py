"""Exact-product public sources. Bounded background work, no guessed specifications."""
import html as html_module
import json
import re
from urllib.parse import urljoin, urlsplit
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from catalog.remote_image import assert_public_http_url, download_public_image
from .kaspi_materials import encode_image, store_image, snapshot, record, customer_description
from .kaspi_catalog_api import CatalogClient, connected


def fetch_page(url):
    for _ in range(4):
        assert_public_http_url(url)
        with requests.get(url,stream=True,allow_redirects=False,timeout=(4,8),headers={'User-Agent':'ZPT-KZ-Card-Materials/1.0 (+https://zpt.kz)'}) as response:
            if response.status_code in (301,302,303,307,308):
                url=urljoin(url,response.headers.get('Location',''))
                continue
            if response.status_code != 200:
                raise ValueError(f'Источник материалов: HTTP {response.status_code}.')
            data=bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data)>2*1024*1024:
                    raise ValueError('Страница источника слишком большая.')
            return data.decode('utf-8',errors='replace')
    raise ValueError('Слишком много перенаправлений источника.')


def exact_products(page, brand, article):
    def walk(value):
        if isinstance(value,list):
            for item in value: yield from walk(item)
        elif isinstance(value,dict):
            kinds=value.get('@type',[])
            if kinds=='Product' or isinstance(kinds,list) and 'Product' in kinds: yield value
            if '@graph' in value: yield from walk(value['@graph'])
    def same(value): return str(value or '').strip().casefold()
    for text in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',page,re.I|re.S):
        try: data=json.loads(html_module.unescape(text))
        except (ValueError,TypeError): continue
        for product in walk(data):
            maker=product.get('brand',{})
            maker=maker.get('name','') if isinstance(maker,dict) else maker
            codes=[product.get(key,'') for key in ('mpn','sku','productID')]
            props=product.get('additionalProperty',[])
            for prop in props if isinstance(props,list) else []:
                if isinstance(prop,dict) and str(prop.get('name','')).casefold() in ('артикул','артикул производителя','manufacturer part number','mpn'):
                    codes.append(prop.get('value',''))
            exact=any(same(code)==same(article) for code in codes)
            if same(maker)==same(brand) and exact:
                yield product


def source_material(page, url, card):
    images=[]; attributes={}; description=''; category=''
    for product in exact_products(page,card.brand,card.article):
        description=description or product.get('description','')
        category=category or str(product.get('category',''))
        photos=product.get('image',[])
        if isinstance(photos,(str,dict)): photos=[photos]
        for photo in photos if isinstance(photos,list) else []:
            if isinstance(photo,dict): photo=photo.get('url') or photo.get('contentUrl')
            if isinstance(photo,str): images.append(urljoin(url,photo))
        props=product.get('additionalProperty',[])
        if isinstance(props,dict): props=[props]
        for prop in props if isinstance(props,list) else []:
            if not isinstance(prop,dict): continue
            name,value=prop.get('name'),prop.get('value')
            if isinstance(name,str) and isinstance(value,(str,int,float,bool)):
                attributes[name]=value
    return dict(images=list(dict.fromkeys(images)),attributes=attributes,description=description,category=category)


CATEGORY_TERMS = [
    (('тормозной диск','тормозные диски'),('тормозн','диск')),
    (('тормозные колодки',),('тормозн','колод')),
    (('пыльник шруса',),('пыльник','шрус')),
    (('стойка стабилизатора',),('стойк','стабилизатор')),
    (('крестовина карданного',),('крестовин',)),
    (('шрус',),('шрус',)), (('сайлентблок',),('сайлентблок',)),
    (('шаровая опора',),('шаров','опор')),
    (('амортизатор капота',),('упор','капот')),
    (('амортизатор',),('амортизатор',)),
    (('тормозной шланг',),('тормозн','шланг')),
    (('патрубок радиатора',),('патруб','радиатор')),
    (('ремкомплект грм',),('грм',)),
]


def choose_category(card, categories):
    text=card.title.casefold()
    for triggers, terms in CATEGORY_TERMS:
        if any(trigger in text for trigger in triggers):
            candidates=[r for r in categories if isinstance(r,dict) and str(r.get('code','')).startswith('Master -')
                and all(term in str(r.get('title','')).casefold() for term in terms)]
            return candidates[0]['code'] if len(candidates)==1 else ''
    return ''


def category_schema(card, attrs, category_hint=''):
    if not connected(): return None
    from django.core.cache import cache
    api=CatalogClient()
    categories=cache.get('kaspi-card-categories')
    if categories is None:
        categories=api.categories()
        if not isinstance(categories,list): raise ValueError('Не получен справочник категорий Kaspi.')
        cache.set('kaspi-card-categories',categories,3600)
    category=card.category
    if not category and category_hint.startswith('Master -') and any(r.get('code')==category_hint for r in categories if isinstance(r,dict)):
        category=category_hint
    category=category or choose_category(card,categories)
    if not category: return None
    import hashlib
    key='kaspi-card-schema:'+hashlib.sha256(category.encode()).hexdigest()
    schema=cache.get(key)
    if schema is None:
        schema=api.attributes(category)
        if not isinstance(schema,list): raise ValueError('Не получены обязательные характеристики Kaspi.')
        cache.set(key,schema,3600)
    # Only exact labels/codes. Unknown enum values, package sizes and dimensions stay unknown.
    supplied={'Бренд':card.brand,'Артикул':card.article,'Артикул производителя':card.article,**card.characteristics,**attrs}
    normalized={str(k).strip().casefold():v for k,v in supplied.items()}
    mapped={}
    for spec in schema:
        if not isinstance(spec,dict) or not spec.get('code'): continue
        code=spec['code']; label=spec.get('title') or spec.get('name')
        if code in supplied: mapped[code]=supplied[code]
        elif label and str(label).strip().casefold() in normalized: mapped[code]=normalized[str(label).strip().casefold()]
    return category,schema,mapped


ENRICHMENT_VERSION = 2


def encode_source_image(data):
    """Pad near-square supplier photos, preserving every source pixel and detail."""
    import io
    from PIL import Image, ImageOps
    # Decode and validate size/format using the same bounded upload pipeline first.
    validated=encode_image(data)
    with Image.open(io.BytesIO(validated[0])) as source:
        if min(source.size)<500 and min(source.size)>=450 and max(source.size)>=500:
            source=ImageOps.pad(source,(max(500,source.width),max(500,source.height)),
                color='white',method=Image.Resampling.LANCZOS)
            out=io.BytesIO();source.save(out,'PNG');data=out.getvalue()
    return encode_image(data,require_size=True)


def due(card):
    if card.source.get('enrichment',{}).get('version') != ENRICHMENT_VERSION: return True
    stamp=card.source.get('enrichment',{}).get('at')
    if not stamp: return True
    try: return timezone.now()-timezone.datetime.fromisoformat(stamp)>timedelta(hours=24)
    except ValueError: return True


def enrich_card(row, card, user, batch_key, batch=None):
    pages=[]; reasons=[]; added=[]; attrs={}; description=''; hint=''
    if 'images' not in card.manual_fields:
        for asset in card.images.filter(active=True).filter(width__gte=450,height__gte=450):
            if min(asset.width,asset.height)<500<=max(asset.width,asset.height):
                encoded=encode_source_image(bytes(asset.data))
                added.append((encoded,asset.source_url,card.source.get('photo_sources',{}).get(str(asset.pk),asset.source_url)))
    if row.get('offer') and row['offer'].supplier_url: pages.append(row['offer'].supplier_url)
    if row.get('offer') and row['offer'].kaspi_url: pages.append(row['offer'].kaspi_url)
    # Photo search supplies source pages; images are accepted only from an exact JSON-LD Product there.
    from catalog.product_image_search import search_product_images
    if getattr(settings,'BRAVE_SEARCH_API_KEY',''):
        search=search_product_images(card.article,title=card.title,brand=card.brand)
        pages.extend(item['source_url'] for item in search.get('images',[])[:3] if item.get('source_url'))
        if not search.get('ok'): reasons.append('Поиск дополнительных фотографий временно недоступен.')
    else:
        reasons.append('Для поиска 2–3 дополнительных фотографий требуется подключённый поиск изображений. Источник поставщика обрабатывается автоматически.')
    if batch and 'images' not in card.manual_fields:
        try:
            for encoded,filename in embedded_images(batch,row):
                added.append((encoded,'','file:'+filename))
                break
        except Exception:
            reasons.append('Не удалось прочитать встроенное фото закупочного файла.')
    revision=card.revision
    existing=set(card.images.filter(active=True).values_list('digest',flat=True))
    for url in list(dict.fromkeys(pages))[:4]:
        try:
            material=source_material(fetch_page(url),url,card)
            attrs.update(material['attributes']); description=description or material['description']; hint=hint or material['category']
            supplier_host=urlsplit(row['offer'].supplier_url).netloc if row.get('offer') else ''
            for image_url in ([] if 'images' in card.manual_fields else material['images'][:1 if urlsplit(url).netloc==supplier_host else 3]):
                data,_=download_public_image(image_url,timeout=8)
                encoded=encode_source_image(data)
                import hashlib
                digest=hashlib.sha256(encoded[0]).hexdigest()
                if digest not in existing:
                    added.append((encoded,image_url,url)); existing.add(digest)
                if len(added)>=4: break
            if len(added)>=4: break
        except Exception:
            reasons.append('Не удалось получить все материалы или подходящее фото: '+urlsplit(url).netloc)
    schema_info=None
    try:
        schema_info=category_schema(card,attrs,hint)
    except ValueError as exc: reasons.append(str(exc))
    if not connected(): reasons.append('Автовыбор категории и обязательных характеристик требует API-ключа магазина Kaspi.')
    from .models import KaspiPreparedCard
    from .kaspi_materials import PRIVATE_KEY, PRIVATE_LINE
    with transaction.atomic():
        current=KaspiPreparedCard.objects.select_for_update().get(pk=card.pk)
        if current.revision!=revision: return False
        before=snapshot(current)
        old_schema=dict(current.source.get('kaspi_schema',{}));old_schema.pop('at',None)
        enriched=set(current.source.get('enriched_fields',[]))
        for encoded,image_url,page_url in added:
            if current.images.filter(active=True).count()>=15: break
            duplicate=similar_image(current,encoded[0])
            if duplicate and min(duplicate.width,duplicate.height)>=500: continue
            asset=store_image(current,encoded,source_url=image_url,replace_id=duplicate.pk if duplicate else None)
            if not page_url.startswith('file:'):
                current.source.setdefault('photo_sources',{})[str(asset.pk)]=page_url
            else: current.source.setdefault('photo_files',{})[str(asset.pk)]=page_url[5:]
            supplier_host=urlsplit(row['offer'].supplier_url).netloc if row.get('offer') else ''
            current.source.setdefault('photo_kinds',{})[str(asset.pk)]=('file' if page_url.startswith('file:') else 'supplier' if urlsplit(page_url).netloc==supplier_host else 'external')
        if attrs and 'characteristics' not in current.manual_fields:
            safe={k:v for k,v in attrs.items() if not PRIVATE_KEY.search(k) and not PRIVATE_LINE.search(k)}
            current.characteristics={**current.characteristics,**safe}; enriched.add('characteristics')
        if description and 'description' not in current.manual_fields:
            current.description=customer_description(re.sub('<[^>]+>',' ',description),row); enriched.add('description')
        if schema_info:
            category,schema,mapped=schema_info
            if 'category' not in current.manual_fields: current.category=category; enriched.add('category')
            current.source['kaspi_schema']=dict(category=category,attributes=schema,values=mapped,at=timezone.now().isoformat())
            # Human descriptions remain independently copyable; API codes are stored separately.
        current.source.update(enriched_fields=sorted(enriched),enrichment=dict(version=ENRICHMENT_VERSION,at=timezone.now().isoformat(),reasons=list(dict.fromkeys(reasons))))
        new_schema=dict(current.source.get('kaspi_schema',{}));new_schema.pop('at',None)
        if before!=snapshot(current) or old_schema!=new_schema:
            current.revision+=1
            current.verified=False
        current.save()
        record(current,user,batch_key,row,'kaspi_card_enriched',before)
    return True


def embedded_images(batch, row):
    """Only images anchored to an XLSX row containing the exact brand and article."""
    import io
    from openpyxl import load_workbook
    obj=batch['obj']
    files=list((obj.attachments if batch['kind']=='receipt' else obj.documents).filter(name__iendswith='.xlsx').only('name','data')[:2])
    for attachment in files:
        if not attachment.name.lower().endswith('.xlsx') or len(attachment.data)>10*1024*1024: continue
        book=load_workbook(io.BytesIO(bytes(attachment.data)),data_only=True)
        for sheet in book:
            matches=set()
            for cells in sheet.iter_rows(values_only=False):
                text=' '.join(str(c.value or '') for c in cells)
                if re.search(r'(?<!\w)'+re.escape(row['article'])+r'(?!\w)',text,re.I) and re.search(r'(?<!\w)'+re.escape(row['brand'])+r'(?!\w)',text,re.I):
                    matches.add(cells[0].row-1)
            for image in getattr(sheet,'_images',[]):
                anchor=getattr(image.anchor,'_from',None)
                if anchor and anchor.row in matches:
                    try: yield encode_source_image(image._data()),attachment.name
                    except ValueError: continue
        book.close()


def similar_image(card, data):
    import io
    from PIL import Image
    def fingerprint(raw):
        with Image.open(io.BytesIO(raw)) as image:
            pixels=list(image.convert('L').resize((9,8)).getdata())
            return [pixels[y*9+x]>pixels[y*9+x+1] for y in range(8) for x in range(8)]
    candidate=fingerprint(data)
    for existing in card.images.filter(active=True).only('thumbnail','width','height'):
        old=fingerprint(bytes(existing.thumbnail))
        if sum(a!=b for a,b in zip(candidate,old))<=4: return existing
    return None
