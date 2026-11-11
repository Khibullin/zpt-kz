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
        return not p.query and bool(re.fullmatch(r'/strut-\d{2}-\d{6}\.html',p.path))
    if p.netloc=='febest.com.ua' and brand=='FEBEST':
        q=parse_qs(p.query,keep_blank_values=True)
        return p.path=='/catalogue/' and set(q)=={'product'} and len(q['product'])==1 and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,119}',q['product'][0]))
    if p.netloc=='www.sankei-555.com' and brand=='555':
        q=parse_qs(p.query)
        return p.path=='/search/detail.php' and set(q)=={'p_id','sch_p_555no'} and len(q['p_id'])==1 and q['p_id'][0].isdigit() and len(q['sch_p_555no'])==1
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
        if p.path!='/strut-'+expected.lower()+'.html':return ''
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
    return ''

