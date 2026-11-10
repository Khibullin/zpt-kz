"""Exact public product photographs from manufacturer catalogues."""
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs,urljoin,urlsplit


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
    if p.netloc=='www.sankei-555.com':
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
    return ''
