"""Exact public product photographs from manufacturer catalogues."""
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs,urljoin,urlsplit,quote


def discovered_page(brand,article):
    """A verified public lookup route; page identity is rechecked before saving."""
    if brand.strip().upper()=='FEBEST' and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,119}',article.strip()):
        return 'https://febest.com.ua/catalogue/?product='+quote(article.strip(),safe='')
    return ''


class Tags(HTMLParser):
    def __init__(self,html):
        super().__init__();self.tags=[];self.feed(html)

    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))


def valid_page(url,brand):
    if not isinstance(url,str):return False
    try:p=urlsplit(url)
    except ValueError:return False
    if p.scheme!='https' or p.fragment:return False
    brand=brand.strip().upper()
    if p.netloc=='cart.bilsteinus.com' and brand=='BILSTEIN':
        return not p.query and bool(re.fullmatch(r'/(?:strut-22|shock-19)-\d{6}\.html',p.path))
    if p.netloc=='febest.com.ua' and brand=='FEBEST':
        q=parse_qs(p.query,keep_blank_values=True)
        return p.path=='/catalogue/' and set(q)=={'product'} and len(q['product'])==1 and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,119}',q['product'][0]))
    if p.netloc=='www.sankei-555.com' and brand=='555':
        q=parse_qs(p.query)
        return p.path=='/search/detail.php' and set(q)=={'p_id','sch_p_555no'} and len(q['p_id'])==1 and q['p_id'][0].isdigit() and len(q['sch_p_555no'])==1
    if p.netloc=='tachka.ru' and brand=='HYUNDAI-KIA':
        return not p.query and bool(re.fullmatch(r'/hyundai-kia/[A-Za-z0-9.-]{1,120}',p.path))
    if p.netloc=='www.autoopt.ru' and brand=='SSANGYONG':
        return not p.query and bool(re.fullmatch(r'/catalog/\d+-[a-z0-9_]+',p.path))
    if p.netloc=='catalog.parts-mall.com' and brand in ('PARTS-MALL','PARTS MALL'):
        q=parse_qs(p.query)
        return p.path=='/shop-detail' and set(q)=={'factory_no'} and len(q['factory_no'])==1 and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,119}',q['factory_no'][0]))
    if p.netloc=='rebornfj.com' and brand=='HAVAL':
        return not p.query and p.path=='/product-detail/front-left-shock-absorber-assembly-for_197499.html'
    if p.query:return False
    return (p.netloc=='asva-trading.com' and brand=='ASVA' and p.path.startswith('/catalog/')) or (
        p.netloc=='aywiparts.ru' and brand=='AYWIPARTS' and bool(re.fullmatch(r'/product/view/\d+',p.path)))


def text(html):
    return unescape(re.sub('<[^>]+>','',html)).strip().upper()


def matching_manufacturer_image(html,url,brand,article):
    if not valid_page(url,brand):return ''
    p=urlsplit(url);expected=article.strip().upper();tags=Tags(html).tags
    if not expected:return ''
    if p.netloc=='cart.bilsteinus.com':
        kind = 'strut' if expected.startswith('22-') else 'shock' if expected.startswith('19-') else ''
        if not kind or p.path!='/'+kind+'-'+expected.lower()+'.html':return ''
        identities=[attrs.get('value') for tag,attrs in tags if tag=='input' and attrs.get('id')=='pro_part_number']
        if identities!=[expected]:return ''
        for tag,attrs in tags:
            if tag!='img' or attrs.get('id')!='pdpMainImg':continue
            src=attrs.get('src','');target=urlsplit(src)
            if target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and re.fullmatch(r'/media/catalog/product/a/p/apa_'+re.escape(expected)+r'_1_\d{14}\.jpg',target.path):return src
    elif p.netloc=='www.sankei-555.com':
        q=parse_qs(p.query)
        if q['sch_p_555no'][0].strip().upper()!=expected:return ''
        row=re.search(r'<th\b[^>]*>\s*555 No\..*?</th>\s*<td\b[^>]*>(.*?)</td>',html,re.S|re.I)
        if not row or text(row.group(1))!=expected:return ''
        for tag,attrs in tags:
            src=urljoin(url,attrs.get('src',''))
            target=urlsplit(src)
            if tag=='img' and attrs.get('id')=='rollover_view' and target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and re.fullmatch(r'/save_img/products/'+q['p_id'][0]+r'/l_\d+\.(?:jpg|jpeg|png|webp)',target.path,re.I):return src
    elif p.netloc=='asva-trading.com':
        # Match the Product object's model, rather than the site's internal mpn.
        for raw in re.findall(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',html,re.S|re.I):
            try:data=json.loads(raw)
            except (TypeError,ValueError):continue
            if not isinstance(data,dict) or data.get('@type')!='Product':continue
            maker=data.get('brand',{})
            if not isinstance(maker,dict) or str(maker.get('name','')).strip().upper()!='ASVA' or str(data.get('model','')).strip().upper()!=expected:continue
            src=data.get('image','')
            if not isinstance(src,str):continue
            target=urlsplit(src)
            if target.path.rsplit('/',1)[-1].rsplit('.',1)[0].casefold() in ('spg','no_image','no-image','noimage','placeholder','default'):continue
            if target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and re.fullmatch(r'/sites/default/files/[A-Za-z0-9_.%() -]+\.(?:jpg|jpeg|png|webp)',target.path,re.I):return src
    elif p.netloc=='aywiparts.ru':
        row=re.search(r'Артикул\s*:\s*<div\b[^>]*class=[\"\']product__info-specifications-item-value[\"\'][^>]*>(.*?)</div>',html,re.S|re.I)
        if not row or text(row.group(1))!=expected:return ''
        for tag,attrs in tags:
            if tag!='a' or 'product-gallery' not in (attrs.get('class') or '').split():continue
            src=urljoin(url,attrs.get('href',''));target=urlsplit(src)
            if target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and target.path.upper()=='/IMAGES/PRODUCT/'+expected+'/MAIN.JPG':return src
    elif p.netloc=='febest.com.ua':
        q=parse_qs(p.query)
        if q['product'][0].upper()!=expected:return ''
        headings=re.findall(r'<h1\b[^>]*>(.*?)</h1>',html,re.I|re.S)
        if not any(text(h)==expected for h in headings):return ''
        if not any(tag=='img' and (attrs.get('alt') or '').strip().upper()=='FEBEST' and 'official-logo' in (attrs.get('class') or '').split() for tag,attrs in tags):return ''
        for tag,attrs in tags:
            if tag!='img' or (attrs.get('alt') or '').strip().upper()!=expected or 'zoomable' not in (attrs.get('class') or '').split():continue
            src=urljoin(url,attrs.get('src',''));target=urlsplit(src)
            params=parse_qs(target.query,keep_blank_values=True)
            if params and not (set(params)=={'v'} and len(params['v'])==1 and params['v'][0].isdigit()):continue
            if target.scheme=='https' and target.netloc==p.netloc and not target.fragment and re.fullmatch(r'/newfolder/product_image/'+re.escape(expected)+r'\.(?:jpg|jpeg|png|webp)',target.path,re.I):return src
    elif p.netloc=='rebornfj.com':
        if expected!='2905110AKZ16A' or p.path!='/product-detail/front-left-shock-absorber-assembly-for_197499.html':return ''
        canonicals=[attrs.get('href') for tag,attrs in tags if tag=='link' and attrs.get('rel')=='canonical']
        if canonicals!=[url]:return ''
        headings=re.findall(r'<h1\b[^>]*>(.*?)</h1>',html,re.I|re.S)
        if len(headings)!=1:
            return ''
        heading=text(headings[0])
        if expected not in heading or 'FRONT LEFT SHOCK ABSORBER ASSEMBLY FOR HAVAL H6 SPORT' not in heading:return ''
        if not re.search(r'Drawing\s+No\..*?'+re.escape(expected),html,re.I|re.S):return ''
        if not re.search(r'Brand.*?Haval',html,re.I|re.S):return ''
        if not re.search(r'SKU.*?'+re.escape(expected),html,re.I|re.S):return ''
        wanted='/wp-content/uploads/2026/05/white_bg_23f1e6e22d5147b5891fd12002e53527.webp'
        for tag,attrs in tags:
            if tag!='img':continue
            src=attrs.get('src','');target=urlsplit(src)
            large=attrs.get('data-large_image','');large_target=urlsplit(large)
            if target.scheme!='https' or target.netloc!=p.netloc or target.query or target.fragment or target.path!=wanted:continue
            if large_target.scheme!='https' or large_target.netloc!=p.netloc or large_target.query or large_target.fragment or large_target.path!=wanted:continue
            if attrs.get('data-large_image_width')!='1000' or attrs.get('data-large_image_height')!='1000':continue
            if (attrs.get('alt') or '')!='white_bg_23f1e6e22d5147b5891fd12002e53527.jpg':continue
            return src
    elif p.netloc=='tachka.ru':
        if p.path.upper()!='/HYUNDAI-KIA/'+expected:return ''
        canonicals=[attrs.get('href') for tag,attrs in tags if tag=='link' and attrs.get('rel')=='canonical']
        if canonicals!=[url]:return ''
        for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.S|re.I):
            try:data=json.loads(raw)
            except (TypeError,ValueError):continue
            if not isinstance(data,dict) or data.get('@type')!='Product':continue
            maker=data.get('brand',{})
            if not isinstance(maker,dict) or str(maker.get('name','')).strip().upper()!='HYUNDAI / KIA':continue
            if str(data.get('sku','')).strip().upper()!=expected or str(data.get('mpn','')).strip().upper()!=expected:continue
            name=str(data.get('name','')).strip().upper()
            if expected not in name or 'HYUNDAI / KIA' not in name:continue
            src=data.get('image','')
            if not isinstance(src,str):continue
            target=urlsplit(src);leaf=target.path.rsplit('/',1)[-1].rsplit('.',1)[0].casefold()
            if leaf in ('spg','no_image','no-image','noimage','placeholder','default'):continue
            if target.scheme=='https' and target.netloc=='img.tachka.ru' and not target.query and not target.fragment and re.search(r'\.(?:jpg|jpeg|png|webp)$',target.path,re.I):return src
    elif p.netloc=='catalog.parts-mall.com':
        q=parse_qs(p.query);factory=q['factory_no'][0].strip().upper()
        if re.sub(r'[-\\s]','',factory)!=re.sub(r'[-\\s]','',expected):return ''
        page_text=text(html)
        if not re.search(r'(?<![A-Z0-9])'+re.escape(factory)+r'(?![A-Z0-9])',page_text):return ''
        if 'PMC' not in page_text or 'SHOCK ABSORBER' not in page_text:return ''
        wanted='/IMAGES/'+factory+'_0300-1F.JPG'
        for tag,attrs in tags:
            if tag!='img':continue
            src=urljoin(url,attrs.get('src',''));target=urlsplit(src)
            if target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and target.path.upper()==wanted:return src
    elif p.netloc=='www.autoopt.ru':
        product_id=p.path.split('/',2)[2].split('-',1)[0]
        canonicals=[attrs.get('href') for tag,attrs in tags if tag=='link' and attrs.get('rel')=='canonical']
        if canonicals!=[url]:return ''
        titles=[attrs.get('content','') for tag,attrs in tags if tag=='meta' and attrs.get('property')=='og:title']
        descriptions=[attrs.get('content','') for tag,attrs in tags if tag=='meta' and attrs.get('property')=='og:description']
        if len(titles)!=1 or len(descriptions)!=1:return ''
        if not re.search(r'(?<!\w)'+re.escape(expected)+r'(?!\w)',titles[0].upper()):return ''
        if 'SSANGYONG' not in titles[0].upper() or not descriptions[0].strip().upper().startswith(expected+' '):return ''
        for tag,attrs in tags:
            if tag!='meta' or attrs.get('property')!='og:image':continue
            src=attrs.get('content','');target=urlsplit(src)
            if target.scheme=='https' and target.netloc==p.netloc and not target.query and not target.fragment and re.fullmatch(r'/product_pictures/big/[a-z0-9]+/'+re.escape(product_id)+r'\.(?:jpg|jpeg|png|webp)',target.path,re.I):return src
    return ''


