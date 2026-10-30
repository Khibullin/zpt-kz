"""Public AG Parts storefront. Filtering never changes supplier or retail data."""
from collections import defaultdict
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.db.models import Q, F, Value, Case, When, CharField
from django.shortcuts import render
from .models import Product, SellerProfile, ProductFitment, Brand, CarModel
from .wholesale import public_stock_status

KEYS = ('country','brand','model','year','engine','category','subgroup','maker','stock','q','sort')
RESET = {'country':('brand','model','year','engine'), 'brand':('model','year','engine'), 'model':('year','engine'), 'year':('engine',), 'category':('subgroup',)}
VEHICLE_PATHS = {
 'country': ('brand__country','selected_brands__country','car_model__brand__country','selected_models__brand__country'),
 'brand': ('brand','selected_brands','car_model__brand','selected_models__brand'),
 'model': ('car_model','selected_models'),
}
FITMENT_PATHS = {'country':'fitments__car_model__brand__country','brand':'fitments__car_model__brand','model':'fitments__car_model'}
LOGOS = {'acura': 'interface/brands/acura.webp', 'audi': 'interface/brands/audi.webp', 'belgee': 'interface/brands/belgee.svg', 'bmw': 'interface/brands/bmw.webp', 'byd': 'interface/brands/byd.webp', 'changan': 'interface/brands/changan.webp', 'chery': 'interface/brands/chery.webp', 'chevrolet': 'interface/brands/chevrolet.webp', 'daihatsu': 'interface/brands/daihatsu.webp', 'exeed': 'interface/brands/exeed.png', 'ford': 'interface/brands/ford.webp', 'geely': 'interface/brands/geely.webp', 'great wall': 'interface/brands/great-wall.webp', 'haval': 'interface/brands/haval.webp', 'honda': 'interface/brands/honda.webp', 'hyundai': 'interface/brands/hyundai.webp', 'infiniti': 'interface/brands/infiniti.webp', 'isuzu': 'interface/brands/isuzu.webp', 'jac': 'interface/brands/jac.webp', 'jaecoo': 'interface/brands/jaecoo.svg', 'jeep': 'interface/brands/jeep.webp', 'jetour': 'interface/brands/jetour.png', 'kaiyi': 'interface/brands/kaiyi.svg', 'kia': 'interface/brands/kia.webp', 'lexus': 'interface/brands/lexus.webp', 'li auto': 'interface/brands/li-auto.png', 'lifan': 'interface/brands/lifan.webp', 'mazda': 'interface/brands/mazda.webp', 'mercedes benz': 'interface/brands/mercedes-benz.webp', 'mercedes-benz': 'interface/brands/mercedes-benz.webp', 'mitsubishi': 'interface/brands/mitsubishi.webp', 'nissan': 'interface/brands/nissan.webp', 'omoda': 'interface/brands/omoda.png', 'peugeot': 'interface/brands/peugeot.webp', 'pontiac': 'interface/brands/pontiac.webp', 'renault samsung': 'interface/brands/renault-samsung.webp', 'seat': 'interface/brands/seat.webp', 'skoda': 'interface/brands/skoda.webp', 'sollers': 'interface/brands/sollers.png', 'subaru': 'interface/brands/subaru.webp', 'suzuki': 'interface/brands/suzuki.webp', 'tank': 'interface/brands/tank.webp', 'tenet': 'interface/brands/tenet.svg', 'toyota': 'interface/brands/toyota.webp', 'vag': 'interface/brands/vag.svg', 'volkswagen': 'interface/brands/vw.webp', 'volvo': 'interface/brands/volvo.webp', 'vw': 'interface/brands/vw.webp', 'zeekr': 'interface/brands/zeekr.png', 'škoda': 'interface/brands/skoda.webp'}


def retail_products():
    seller = SellerProfile.objects.filter(slug='ag-parts').first()
    return Product.objects.filter(status='active').owned_by_seller(seller).annotate(
        retail_maker=Case(When(omega_listing__offer__brand__gt='',then=F('omega_listing__offer__brand')),When(title__icontains='AG Parts',then=Value('AG Parts')),default=Value('Не указан'),output_field=CharField()),
        retail_subgroup=Case(
            When(title__iregex=r'фильтр.*масл|маслян.*фильтр',then=Value('Фильтры масляные')),
            When(title__iregex=r'фильтр.*возд|воздуш.*фильтр',then=Value('Фильтры воздушные')),
            When(title__iregex=r'фильтр.*салон|салон.*фильтр',then=Value('Фильтры салонные')),
            When(title__iregex=r'свеч',then=Value('Свечи зажигания')),
            default=F('category__name'),output_field=CharField()))


def apply_filters(qs, selected, omit=()):
    if selected['model'].isdecimal() and 'model' not in omit:
        model=CarModel.objects.filter(pk=selected['model']).values('brand_id','brand__country_id').first()
        if not model or (selected['brand'] and 'brand' not in omit and str(model['brand_id'])!=selected['brand']) or (selected['country'] and 'country' not in omit and str(model['brand__country_id'])!=selected['country']):
            return qs.none()
    structured, legacy = {}, Q()
    vehicle = False
    for key in ('country','brand','model'):
        value = selected[key] if key not in omit else ''
        if not value:
            continue
        if not value.isdecimal():
            return qs.none()
        vehicle = True
        structured[FITMENT_PATHS[key].removeprefix('fitments__')+'__pk'] = value
        clause = Q()
        for path in VEHICLE_PATHS[key]:
            clause |= Q(pk__in=Product.objects.filter(**{path+'__pk':value}).values('pk'))
        legacy &= clause
    if selected['year'] and 'year' not in omit:
        value=selected['year']
        if not value.isdecimal() or not 1900 <= int(value) <= 2100:
            return qs.none()
        year=int(value)
        vehicle=True
        structured['year_from__lte']=year
        # Closed periods or open periods checked in this year or later.
        period=Q(year_to__gte=year)|Q(year_to__isnull=True,verified_on__year__gte=year)
        legacy=Q(pk__in=[])
    else:
        period=Q()
    if selected['engine'] and 'engine' not in omit:
        vehicle=True
        engine=Q(engine_code=selected['engine'])|Q(engine_policy='independent')
        legacy=Q(pk__in=[])
    else:
        engine=Q()
    if vehicle:
        rows=ProductFitment.objects.filter(status='confirmed',**structured).filter(period&engine)
        confirmed=Q(fitment_reviewed_on__isnull=False,pk__in=rows.values('product_id'))
        qs=qs.filter(confirmed | (Q(fitment_reviewed_on__isnull=True)&legacy))
    for key,field in [('category','category_id'),('subgroup','retail_subgroup'),('maker','retail_maker')]:
        if selected[key] and key not in omit:
            if key=='category' and not selected[key].isdecimal():
                return qs.none()
            qs=qs.filter(**{field:selected[key]})
    if selected['stock']=='in':
        qs=qs.filter(stock_qty__gt=0)
    if selected['q']:
        from .article_utils import filter_products_by_public_query
        qs=filter_products_by_public_query(qs,selected['q'])
    return qs.distinct()


def storefront(request):
    base=retail_products()
    selected={key:request.GET.get(key,'').strip()[:200] for key in KEYS}
    def url(**changes):
        return '/?'+urlencode({**{k:v for k,v in {**selected,**changes}.items() if v!=''}, 'picker':'1'})
    def facet(key):
        if key=='model' and not selected['brand']:
            return []
        if key in ('year','engine') and not selected['model']:
            return []
        scope=apply_filters(base,selected,(key,*RESET.get(key,())))
        values=defaultdict(set)
        labels={}
        if key in VEHICLE_PATHS:
            sources=[(scope.filter(fitment_reviewed_on__isnull=True),p) for p in VEHICLE_PATHS[key]]
            sources.append((scope.filter(fitment_reviewed_on__isnull=False,fitments__status='confirmed'),FITMENT_PATHS[key]))
            for qs,path in sources:
                for pk,value,label in qs.order_by().values_list('pk',path+'__pk',path+'__name').distinct():
                    if value is not None:
                        value=str(value); values[value].add(pk); labels[value]=label
        elif key in ('year','engine'):
            rows=ProductFitment.objects.filter(product_id__in=scope.values('pk'),product__fitment_reviewed_on__isnull=False,status='confirmed')
            if selected['model'].isdecimal():rows=rows.filter(car_model_id=selected['model'])
            if selected['brand'].isdecimal():rows=rows.filter(car_model__brand_id=selected['brand'])
            if key=='engine' and selected['year'].isdecimal():
                year=int(selected['year']); rows=rows.filter(year_from__lte=year).filter(Q(year_to__gte=year)|Q(year_to__isnull=True,verified_on__year__gte=year))
            for row in rows:
                if key=='engine':
                    if row.engine_code:values[row.engine_code].add(row.product_id)
                elif row.year_from:
                    end=row.year_to or (row.verified_on.year if row.verified_on else row.year_from)
                    for year in range(row.year_from,min(end,2100)+1):values[str(year)].add(row.product_id)
        else:
            field={'category':'category_id','subgroup':'retail_subgroup','maker':'retail_maker'}[key]
            fields=('pk',field,'category__name') if key=='category' else ('pk',field)
            for row in scope.order_by().values_list(*fields).distinct():
                if row[1]:
                    value=str(row[1]);values[value].add(row[0]);labels[value]=row[2] if key=='category' else value
        if key=='brand':
            automotive={str(pk) for pk in Brand.objects.filter(models__isnull=False).values_list('pk',flat=True).distinct()}
            values={v:ids for v,ids in values.items() if v in automotive}
        if key=='brand' and selected['country'].isdecimal():
            allowed={str(pk) for pk in Brand.objects.filter(country_id=selected['country']).values_list('pk',flat=True)}
            values={v:ids for v,ids in values.items() if v in allowed}
        if key=='model' and selected['brand'].isdecimal():
            allowed={str(pk) for pk in CarModel.objects.filter(brand_id=selected['brand']).values_list('pk',flat=True)}
            values={v:ids for v,ids in values.items() if v in allowed}
        return [dict(value=v,label=labels.get(v,v),count=len(ids),selected=selected[key]==v,
                     url=url(**{key:'' if selected[key]==v else v,**{r:'' for r in RESET.get(key,())}}),
                     logo=LOGOS.get(labels.get(v,v).casefold(),''))
                for v,ids in sorted(values.items(),key=lambda item:labels.get(item[0],item[0]).casefold())]
    facets={k:facet(k) for k in ('country','brand','model','year','engine','category','subgroup','maker')}
    found=apply_filters(base,selected).select_related('seller_profile','brand','car_model__brand','category').prefetch_related('kaspi_listings','selected_models__brand','selected_brands','fitments__car_model__brand')
    order={'price':F('price').asc(nulls_last=True),'-price':F('price').desc(nulls_last=True)}.get(selected['sort'],'title')
    page=Paginator(found.order_by(order,'pk'),24).get_page(request.GET.get('page'))
    for product in page:
        product.public_stock=public_stock_status(product)
        product.has_public_wholesale=False
    chips=[]
    for key in ('country','brand','model','year','engine','category','subgroup','maker','q'):
        if selected[key]:
            label=next((o['label'] for o in facets.get(key,[]) if o['value']==selected[key]),selected[key])
            chips.append(dict(label=label,url=url(**{key:'',**{r:'' for r in RESET.get(key,())}})))
    return render(request,'catalog/interface/storefront.html',dict(interface_home=True,hide_product_search=True,
        facets=facets,selected=selected,chips=chips,products=page,result_count=page.paginator.count,
        previous_url=url(page=page.previous_page_number()) if page.has_previous() else '',
        next_url=url(page=page.next_page_number()) if page.has_next() else '',
        filter_groups=[dict(key=k,title=t,options=facets[k]) for k,t in [('category','Категория'),('subgroup','Подгруппа'),('brand','Марка авто'),('model','Модель'),('maker','Производитель')]]))
