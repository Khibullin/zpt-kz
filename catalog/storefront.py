"""Public AG Parts storefront. Filtering never changes supplier or retail data."""
from collections import defaultdict
from hashlib import sha256
from time import perf_counter
import logging
from django.core.cache import caches
from urllib.parse import urlencode
from django.core.paginator import Paginator
from django.db.models import Q, F, Value, Case, When, CharField, Count, IntegerField, Window
from django.db.models.functions import RowNumber
from django.db import connection
from django.core.exceptions import EmptyResultSet
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
LOGOS = {'acura': 'interface/brands/acura.webp', 'audi': 'interface/brands/audi.webp', 'belgee': 'interface/brands/belgee.svg', 'bmw': 'interface/brands/bmw.webp', 'byd': 'interface/brands/byd.webp', 'changan': 'interface/brands/changan.webp', 'chery': 'interface/brands/chery.webp', 'chevrolet': 'interface/brands/chevrolet.webp', 'daihatsu': 'interface/brands/daihatsu.webp', 'exeed': 'interface/brands/exeed.png', 'ford': 'interface/brands/ford.webp', 'geely': 'interface/brands/geely.webp', 'great wall': 'interface/brands/great-wall.png', 'haval': 'interface/brands/haval.webp', 'honda': 'interface/brands/honda.webp', 'hyundai': 'interface/brands/hyundai.webp', 'infiniti': 'interface/brands/infiniti.webp', 'isuzu': 'interface/brands/isuzu.webp', 'jac': 'interface/brands/jac.webp', 'jaecoo': 'interface/brands/jaecoo.svg', 'jeep': 'interface/brands/jeep.webp', 'jetour': 'interface/brands/jetour.png', 'kaiyi': 'interface/brands/kaiyi.svg', 'kia': 'interface/brands/kia.webp', 'lexus': 'interface/brands/lexus.webp', 'li auto': 'interface/brands/li-auto.png', 'lifan': 'interface/brands/lifan.webp', 'mazda': 'interface/brands/mazda.webp', 'mercedes benz': 'interface/brands/mercedes-benz.webp', 'mercedes-benz': 'interface/brands/mercedes-benz.webp', 'mitsubishi': 'interface/brands/mitsubishi.webp', 'nissan': 'interface/brands/nissan.webp', 'omoda': 'interface/brands/omoda.png', 'peugeot': 'interface/brands/peugeot.webp', 'pontiac': 'interface/brands/pontiac.webp', 'renault samsung': 'interface/brands/renault-samsung.webp', 'seat': 'interface/brands/seat.webp', 'skoda': 'interface/brands/skoda.webp', 'sollers': 'interface/brands/sollers.png', 'subaru': 'interface/brands/subaru.webp', 'suzuki': 'interface/brands/suzuki.webp', 'tank': 'interface/brands/tank.svg', 'tenet': 'interface/brands/tenet.svg', 'toyota': 'interface/brands/toyota.webp', 'vag': 'interface/brands/vag.svg', 'volkswagen': 'interface/brands/vw.webp', 'volvo': 'interface/brands/volvo.webp', 'vw': 'interface/brands/vw.webp', 'zeekr': 'interface/brands/zeekr.png', 'škoda': 'interface/brands/skoda.webp'}


def retail_products():
    seller = SellerProfile.objects.filter(slug='ag-parts').first()
    return Product.objects.filter(status='active').owned_by_seller(seller)


def retail_annotations(qs):
    # Add supplier/title expressions only where their values are used. Keeping
    # them on the base queryset forces supplier joins into every COUNT/facet.
    return qs.annotate(
        retail_maker=Case(When(omega_listing__offer__brand__gt='',then=F('omega_listing__offer__brand')),When(title__icontains='AG Parts',then=Value('AG Parts')),default=Value('Не указан'),output_field=CharField()),
        retail_subgroup=Case(
            When(title__iregex=r'фильтр.*масл|маслян.*фильтр',then=Value('Фильтры масляные')),
            When(title__iregex=r'фильтр.*возд|воздуш.*фильтр',then=Value('Фильтры воздушные')),
            When(title__iregex=r'фильтр.*салон|салон.*фильтр',then=Value('Фильтры салонные')),
            When(title__iregex=r'свеч',then=Value('Свечи зажигания')),
            default=F('category__name'),output_field=CharField()))


def photo_ordered_products(qs):
    """Stable category rotation, also prepared by the existing snapshot consumer."""
    return qs.annotate(storefront_photo_rank=Case(
        When(Q(main_image__isnull=True)|Q(main_image=''),then=Value(1)),
        default=Value(0),output_field=IntegerField())).annotate(
        storefront_category_row=Window(expression=RowNumber(),
            partition_by=[F('category_id'),F('storefront_photo_rank')],
            order_by=[F('title').asc(),F('pk').asc()])).order_by(
                'storefront_photo_rank','storefront_category_row','category__name','title','pk')


def apply_filters(qs, selected, omit=()):
    model=None
    if selected['model'].isdecimal() and 'model' not in omit:
        model=CarModel.objects.filter(pk=selected['model']).values('brand_id','brand__country_id').first()
        if not model or (selected['brand'] and 'brand' not in omit and str(model['brand_id'])!=selected['brand']) or (selected['country'] and 'country' not in omit and str(model['brand__country_id'])!=selected['country']):
            return qs.none()
    brand_in_country=False
    if (model is None and selected['country'].isdecimal() and selected['brand'].isdecimal()
            and 'country' not in omit and 'brand' not in omit):
        brand_country=Brand.objects.filter(pk=selected['brand']).values_list('country_id',flat=True).first()
        brand_in_country=str(brand_country)==selected['country']
    structured, legacy_ids = {}, None
    legacy_allowed=not ((selected['year'] and 'year' not in omit) or (selected['engine'] and 'engine' not in omit))
    vehicle = False
    for key in ('country','brand','model'):
        # A model relationship already implies its brand and country; a
        # brand implies its own country. The validation above preserves
        # mismatched selections, including legacy multi-brand products.
        if (model is not None and key in ('country','brand')) or (brand_in_country and key=='country'):
            continue
        value = selected[key] if key not in omit else ''
        if not value:
            continue
        if not value.isdecimal():
            return qs.none()
        vehicle = True
        structured[FITMENT_PATHS[key].removeprefix('fitments__')+'__pk'] = value
        if legacy_allowed:
            paths = [Product.objects.filter(**{path+'__pk':value}).order_by().values_list('pk',flat=True)
                     for path in VEHICLE_PATHS[key]]
            # Materialize narrow relationship IDs before the ownership filter.
            # Otherwise PostgreSQL overestimates the UNION cardinality and scans
            # the whole wide Product table for each country/brand selection.
            ids=set(paths[0].union(*paths[1:]))
            legacy_ids=ids if legacy_ids is None else legacy_ids & ids
    if selected['year'] and 'year' not in omit:
        value=selected['year']
        if not value.isdecimal() or not 1900 <= int(value) <= 2100:
            return qs.none()
        year=int(value)
        vehicle=True
        structured['year_from__lte']=year
        # Closed periods or open periods checked in this year or later.
        period=Q(year_to__gte=year)|Q(year_to__isnull=True,verified_on__year__gte=year)
        legacy_allowed=False
    else:
        period=Q()
    if selected['engine'] and 'engine' not in omit:
        vehicle=True
        engine=Q(engine_code=selected['engine'])|Q(engine_policy='independent')
        legacy_allowed=False
    else:
        engine=Q()
    if vehicle:
        rows=ProductFitment.objects.filter(status='confirmed',**structured).filter(period&engine)
        matching_ids=set(rows.filter(product__fitment_reviewed_on__isnull=False).order_by().values_list('product_id',flat=True))
        if legacy_allowed and legacy_ids:
            matching_ids.update(Product.objects.filter(fitment_reviewed_on__isnull=True,
                pk__in=legacy_ids).order_by().values_list('pk',flat=True))
        qs=qs.filter(pk__in=matching_ids)
    if any(selected[key] and key not in omit for key in ('subgroup','maker')):
        qs=retail_annotations(qs)
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
    # All to-many vehicle matching uses PK subqueries. The base annotation
    # joins only a one-to-one listing, so DISTINCT just sorts wide product rows.
    return qs


def vehicle_facet_counts(sources):
    """Deduplicate product/vehicle pairs in SQL, without retaining every product ID."""
    queries = [qs.order_by().annotate(facet_product=F('pk'), facet_value=F(path+'__pk'),
        facet_label=F(path+'__name')).values('facet_product','facet_value','facet_label') for qs,path in sources]
    combined = queries[0].union(*queries[1:])
    try:
        sql, params = combined.query.sql_with_params()
    except EmptyResultSet:
        return []
    with connection.cursor() as cursor:
        cursor.execute('SELECT facet_value, facet_label, COUNT(DISTINCT facet_product) FROM (' + sql +
            ') facet_rows WHERE facet_value IS NOT NULL GROUP BY facet_value, facet_label', params)
        return cursor.fetchall()


def root_vehicle_facet_counts(base):
    """Read shared vehicle relationships once for the home brand/country counts."""
    sources=[(base.filter(fitment_reviewed_on__isnull=True),p) for p in VEHICLE_PATHS['brand']]
    sources.append((base.filter(fitment_reviewed_on__isnull=False,fitments__status='confirmed'),FITMENT_PATHS['brand']))
    queries=[qs.order_by().annotate(facet_product=F('pk'),facet_brand=F(path+'__pk'),
        brand_label=F(path+'__name'),facet_country=F(path+'__country__pk'),
        country_label=F(path+'__country__name')).values(
            'facet_product','facet_brand','brand_label','facet_country','country_label') for qs,path in sources]
    try:
        sql,params=queries[0].union(*queries[1:]).query.sql_with_params()
    except EmptyResultSet:
        return []
    with connection.cursor() as cursor:
        cursor.execute("WITH vehicle_rows AS MATERIALIZED ("+sql+") "
            "SELECT 'brand',facet_brand,brand_label,COUNT(DISTINCT facet_product) FROM vehicle_rows "
            "WHERE facet_brand IS NOT NULL GROUP BY facet_brand,brand_label UNION ALL "
            "SELECT 'country',facet_country,country_label,COUNT(DISTINCT facet_product) FROM vehicle_rows "
            "WHERE facet_country IS NOT NULL GROUP BY facet_country,country_label",params)
        return cursor.fetchall()


def facet_values(key, base, selected, filter_selection, root_rows=None, scope=None):
    if scope is None:
        scope=apply_filters(base,filter_selection,(key,*RESET.get(key,())))
    values=defaultdict(set)
    labels={}
    if key in ('country','brand') and not any(selected[k] for k in KEYS if k!='sort'):
        rows=root_rows if root_rows is not None else root_vehicle_facet_counts(base)
        for kind,value,label,count in rows:
            if kind==key:
                value=str(value);values[value]=count;labels[value]=label
    elif key in VEHICLE_PATHS:
        sources=[(scope.filter(fitment_reviewed_on__isnull=True),p) for p in VEHICLE_PATHS[key]]
        sources.append((scope.filter(fitment_reviewed_on__isnull=False,fitments__status='confirmed'),FITMENT_PATHS[key]))
        for value,label,count in vehicle_facet_counts(sources):
            value=str(value); values[value]=count; labels[value]=label
    elif key in ('year','engine'):
        rows=ProductFitment.objects.filter(product_id__in=scope.values('pk'),product__fitment_reviewed_on__isnull=False,status='confirmed')
        if selected['model'].isdecimal():rows=rows.filter(car_model_id=selected['model'])
        if selected['brand'].isdecimal():rows=rows.filter(car_model__brand_id=selected['brand'])
        if key=='engine' and selected['year'].isdecimal():
            year=int(selected['year']); rows=rows.filter(year_from__lte=year).filter(Q(year_to__gte=year)|Q(year_to__isnull=True,verified_on__year__gte=year))
        for row in rows.iterator(chunk_size=200):
            if key=='engine':
                if row.engine_code:values[row.engine_code].add(row.product_id)
            elif row.year_from:
                end=row.year_to or (row.verified_on.year if row.verified_on else row.year_from)
                for year in range(row.year_from,min(end,2100)+1):values[str(year)].add(row.product_id)
    else:
        if key in ('subgroup','maker'):
            scope=retail_annotations(scope)
        field={'category':'category_id','subgroup':'retail_subgroup','maker':'retail_maker'}[key]
        fields=(field,'category__name') if key=='category' else (field,)
        for row in scope.order_by().values(*fields).annotate(_facet_count=Count('pk',distinct=True)):
            if row[field]:
                value=str(row[field]); values[value]=row['_facet_count']; labels[value]=row['category__name'] if key=='category' else value
    if key=='brand':
        automotive={str(pk) for pk in Brand.objects.filter(models__isnull=False).values_list('pk',flat=True).distinct()}
        values={v:ids for v,ids in values.items() if v in automotive}
    if key=='brand' and selected['country'].isdecimal():
        allowed={str(pk) for pk in Brand.objects.filter(country_id=selected['country']).values_list('pk',flat=True)}
        values={v:ids for v,ids in values.items() if v in allowed}
    if key=='model' and selected['brand'].isdecimal():
        allowed={str(pk) for pk in CarModel.objects.filter(brand_id=selected['brand']).values_list('pk',flat=True)}
        values={v:ids for v,ids in values.items() if v in allowed}
    return {v:len(ids) if isinstance(ids,set) else ids for v,ids in values.items()}, labels

def load_home_facets():
    """A public request never computes whole-catalogue facet aggregations."""
    from .models import StorefrontFacetSnapshot, Country, Category
    payload=StorefrontFacetSnapshot.objects.filter(pk='home').values_list('payload',flat=True).first()
    if payload and payload.get('version')==1:
        return {**payload['facets'], '_home_order':payload.get('home_order')}
    # Fast first-deploy fallback. The existing consumer builds counts shortly;
    # options without a count remain usable and never pretend there are 0 items.
    def options(rows):
        rows=list(rows)
        return ({str(pk):None for pk,label in rows},{str(pk):label for pk,label in rows})
    return {'country':options(Country.objects.values_list('pk','name')),
            'brand':options(Brand.objects.filter(models__isnull=False).values_list('pk','name').distinct()),
            'category':options(Category.objects.values_list('pk','name'))}


def build_home_facets():
    """Run only in the existing consumer or an explicit maintenance command."""
    base=retail_products()
    selected={key:'' for key in KEYS}
    rows=root_vehicle_facet_counts(base)
    ordered_ids=list(photo_ordered_products(base).values_list('pk',flat=True))
    return {'version':1,'facets':{key:facet_values(key,base,selected,selected,rows)
        for key in ('country','brand','category','subgroup','maker')},
        'home_order':dict(ids=ordered_ids,count=len(ordered_ids),full_order=True)}


def refresh_home_facets(force=False):
    from datetime import timedelta
    from django.db import transaction
    from django.utils import timezone
    from .models import StorefrontFacetSnapshot
    # Refresh one shared row at a time. Readers keep the last complete result
    # while this transaction calculates and atomically replaces the snapshot.
    StorefrontFacetSnapshot.objects.get_or_create(pk='home')
    with transaction.atomic():
        snapshot=StorefrontFacetSnapshot.objects.select_for_update(skip_locked=True).filter(pk='home').first()
        if snapshot is None:
            return False
        if (not force and snapshot.payload.get('home_order') is not None and snapshot.built_at
                and timezone.now()-snapshot.built_at<timedelta(minutes=5)):
            return False
        snapshot.payload=build_home_facets()
        snapshot.built_at=timezone.now()
        snapshot.save(update_fields=['payload','built_at'])
    return True


def refresh_country_facets():
    """Prepare one public country selection, using the existing idle consumer."""
    from datetime import timedelta
    from django.db import transaction
    from django.utils import timezone
    from .models import Country, StorefrontFacetSnapshot
    cutoff=timezone.now()-timedelta(minutes=5)
    ready=set(StorefrontFacetSnapshot.objects.filter(key__startswith='country:',
        built_at__gte=cutoff).values_list('key',flat=True))
    country_id=next((pk for pk in Country.objects.order_by('pk').values_list('pk',flat=True)
                     if f'country:{pk}' not in ready),None)
    if country_id is None:
        return False
    key=f'country:{country_id}'
    StorefrontFacetSnapshot.objects.get_or_create(pk=key)
    with transaction.atomic():
        snapshot=StorefrontFacetSnapshot.objects.select_for_update(skip_locked=True).filter(pk=key).first()
        if snapshot is None or (snapshot.built_at and snapshot.built_at>=cutoff):
            return False
        base=retail_products()
        selected={k:'' for k in KEYS}
        selected['country']=str(country_id)
        ids=set(apply_filters(base,selected).order_by().values_list('pk',flat=True))
        scope=Product.objects.filter(pk__in=ids)
        root=load_home_facets()
        values,labels=root.get('brand',({},{}))
        allowed={str(pk) for pk in Brand.objects.filter(country_id=country_id).values_list('pk',flat=True)}
        facets={'country':root['country'],
                'brand':({pk:count for pk,count in values.items() if pk in allowed},labels)}
        for facet in ('category','subgroup','maker'):
            facets[facet]=facet_values(facet,base,selected,selected,scope=scope)
        root_ids=(root.get('_home_order') or {}).get('ids',[])
        ordered=[pk for pk in root_ids if pk in ids]
        missing=ids-set(ordered)
        if missing:
            ordered.extend(photo_ordered_products(scope.filter(pk__in=missing)).values_list('pk',flat=True))
        snapshot.payload={'version':1,'facets':facets,
            'home_order':dict(ids=ordered,count=len(ordered),full_order=True)}
        snapshot.built_at=timezone.now()
        snapshot.save(update_fields=['payload','built_at'])
    return True


def refresh_brand_facets():
    """Prepare one make and its model list outside visitor requests."""
    from datetime import timedelta
    from django.db import transaction
    from django.utils import timezone
    from .models import StorefrontFacetSnapshot
    root=load_home_facets()
    counts=root.get('brand',({},{}))[0]
    cutoff=timezone.now()-timedelta(minutes=5)
    ready=set(StorefrontFacetSnapshot.objects.filter(key__startswith='brand:',
        built_at__gte=cutoff).values_list('key',flat=True))
    candidates=sorted(counts,key=lambda pk:-(counts[pk] or 0))
    brand_id=next((pk for pk in candidates if f'brand:{pk}' not in ready),None)
    if brand_id is None:
        return False
    brand=Brand.objects.filter(pk=brand_id).first()
    if brand is None:
        return False
    key=f'brand:{brand_id}'
    StorefrontFacetSnapshot.objects.get_or_create(pk=key)
    with transaction.atomic():
        snapshot=StorefrontFacetSnapshot.objects.select_for_update(skip_locked=True).filter(pk=key).first()
        if snapshot is None or (snapshot.built_at and snapshot.built_at>=cutoff):
            return False
        base=retail_products()
        selected={k:'' for k in KEYS}
        selected['brand']=str(brand_id)
        ids=set(apply_filters(base,selected).order_by().values_list('pk',flat=True))
        scope=Product.objects.filter(pk__in=ids)
        facets={facet:facet_values(facet,base,selected,selected,scope=scope)
                for facet in ('model','category','subgroup','maker')}
        root_ids=(root.get('_home_order') or {}).get('ids',[])
        ordered=[pk for pk in root_ids if pk in ids]
        missing=ids-set(ordered)
        if missing:
            ordered.extend(photo_ordered_products(scope.filter(pk__in=missing)).values_list('pk',flat=True))
        snapshot.payload={'version':1,'country_id':str(brand.country_id),'facets':facets,
            'home_order':dict(ids=ordered,count=len(ordered),full_order=True)}
        snapshot.built_at=timezone.now()
        snapshot.save(update_fields=['payload','built_at'])
    return True


def vehicle_navigation():
    """Compact public picker options only: no products, prices or user data."""
    from django.templatetags.static import static
    from .models import StorefrontFacetSnapshot
    cache=caches['storefront_common']
    data=cache.get('vehicle-navigation-v1')
    if data is not None:
        return data
    rows=StorefrontFacetSnapshot.objects.filter(key__startswith='brand:').values_list(
        'key','payload__facets__model','payload__home_order__count')
    snapshots={key.split(':',1)[1]:(models,count) for key,models,count in rows if models}
    data={}
    for brand in Brand.objects.filter(pk__in=snapshots).values('pk','name','country_id'):
        model_data,count=snapshots[str(brand['pk'])]
        values,labels=model_data
        logo=LOGOS.get(brand['name'].casefold(),'')
        data[str(brand['pk'])]=dict(name=brand['name'],country=str(brand['country_id']),
            count=count,logo=static(logo) if logo else '',
            models=[dict(value=value,label=labels.get(value,value),count=total)
                    for value,total in sorted(values.items(),key=lambda row:labels.get(row[0],row[0]).casefold())])
    cache.set('vehicle-navigation-v1',data,60)
    return data


def storefront(request):
    started=perf_counter()
    base=retail_products()
    selected={key:request.GET.get(key,'').strip()[:200] for key in KEYS}
    # Resolve exact article/OEM candidates once, rather than once per facet.
    filter_selection={**selected, 'q':''}
    if selected['q']:
        from .article_utils import filter_products_by_public_query
        base=filter_products_by_public_query(base, selected['q'])
    def url(**changes):
        params={k:v for k,v in {**selected,**changes}.items() if v!=''}
        if any(params.get(k) for k in KEYS if k!='sort'):
            params['picker']='1'
        return '/?'+urlencode(params) if params else '/'
    common=not any(selected[k] for k in KEYS if k!='sort')
    home_facets=load_home_facets()
    country_snapshot=False
    prepared_country=None
    if selected['country'].isdecimal():
        from .models import StorefrontFacetSnapshot
        payload=StorefrontFacetSnapshot.objects.filter(pk='country:'+selected['country']).values_list('payload',flat=True).first()
        if payload and payload.get('version')==1 and 'home_order' in payload:
            prepared_country=payload
            if not any(selected[k] for k in KEYS if k not in ('country','sort')):
                home_facets={**payload['facets'],'_home_order':payload['home_order']}
                country_snapshot=True
    prepared_brand=None
    brand_snapshot=False
    if selected['brand'].isdecimal():
        from .models import StorefrontFacetSnapshot
        payload=StorefrontFacetSnapshot.objects.filter(pk='brand:'+selected['brand']).values_list('payload',flat=True).first()
        if (payload and payload.get('version')==1 and 'home_order' in payload and
                (not selected['country'] or selected['country']==payload.get('country_id'))):
            prepared_brand=payload
            brand_snapshot=not any(selected[k] for k in KEYS if k not in ('country','brand','sort'))
    scopes={}
    def scope_for(omit=()):
        effective={k:('' if k in omit else v) for k,v in filter_selection.items()}
        scope_key=tuple(effective.items())
        if scope_key not in scopes:
            # Resolve vehicle relationships once per distinct filter scope in
            # this request. Facets and pagination reuse these narrow IDs; all
            # prices/availability are still read fresh when hydrating cards.
            ids=list(apply_filters(base,effective).order_by().values_list('pk',flat=True))
            scopes[scope_key]=Product.objects.filter(pk__in=ids)
        return scopes[scope_key]
    def facet(key):
        if key=='model' and not selected['brand']:
            return []
        if key in ('year','engine') and not selected['model']:
            return []
        omit=(key,*RESET.get(key,()))
        effective={k:selected[k] for k in KEYS if k!='sort' and k not in omit}
        cache_key='v3:'+key+':'+sha256(urlencode(effective).encode()).hexdigest()
        if country_snapshot or not any(effective.values()):
            values,labels=home_facets.get(key, ({},{}))
            return facet_options(key,values,labels)
        if (prepared_country is not None and
                {k for k,v in effective.items() if v}=={'country'} and
                key in prepared_country['facets']):
            # Selecting a child must not rebuild its unchanged parent facet.
            values,labels=prepared_country['facets'][key]
            return facet_options(key,values,labels)
        if (prepared_brand is not None and key in prepared_brand['facets'] and
                {k for k,v in effective.items() if v}<={'country','brand'} and effective.get('brand')):
            values,labels=prepared_brand['facets'][key]
            return facet_options(key,values,labels)
        # Filter-specific counters retain their short, bounded cache.
        facet_cache=caches['storefront_common' if common else 'storefront_facets']
        cached=facet_cache.get(cache_key)
        if cached is not None:
            values,labels=cached
        else:
            values,labels=facet_values(key,base,selected,filter_selection,scope=scope_for(omit))
            facet_cache.set(cache_key,(values,labels),120)
        return facet_options(key,values,labels)
    def facet_options(key,values,labels):
        return [dict(value=v,label=labels.get(v,v),count=count,selected=selected[key]==v,
                     url=url(**{key:'' if selected[key]==v else v,**{r:'' for r in RESET.get(key,())}}),
                     logo=LOGOS.get(labels.get(v,v).casefold(),''))
                for v,count in sorted(values.items(),key=lambda item:labels.get(item[0],item[0]).casefold())]
    facets={k:facet(k) for k in ('country','brand','model','year','engine','category','subgroup','maker')}
    facets_finished=perf_counter()
    found=(base.filter(pk__in=prepared_brand['home_order']['ids']) if brand_snapshot else
           base.filter(pk__in=home_facets['_home_order']['ids']) if country_snapshot else
           base if common else scope_for()).select_related('seller_profile','brand','car_model__brand','category').prefetch_related('kaspi_listings','selected_models__brand','selected_brands','fitments__car_model__brand')
    order={'price':F('price').asc(nulls_last=True),'-price':F('price').desc(nulls_last=True),'title':'title'}.get(selected['sort'])
    page_key='home-page-v1:'+str(request.GET.get('page','1'))[:12]
    page_cache=caches['storefront_common']
    cached_page=page_cache.get(page_key) if common and order is None else None
    if (common or country_snapshot) and order is None:
        # The shared page ordering survives process restarts. This contains only IDs
        # and counters; actual card data is always loaded from the live DB.
        cached_page=home_facets.get('_home_order') or cached_page
    if brand_snapshot and order is None:
        cached_page=prepared_brand['home_order']
    if order is None:
        if cached_page is None:
            found=photo_ordered_products(found)
            ordering=found.query.order_by
        else:
            ordering=('pk',)
    else:
        ordering=(order,'pk')
    # Sort a narrow ID projection; evaluate costly annotations and load related
    # data only for the current 24 cards, not the entire catalogue.
    paginator=Paginator(found.order_by(*ordering).values_list('pk',flat=True),24)
    if cached_page is not None:
        paginator.count=cached_page['count']
    page=paginator.get_page(request.GET.get('page'))
    if cached_page is not None:
        page_ids=(cached_page['ids'][page.start_index()-1:page.end_index()]
                  if cached_page.get('full_order') else cached_page['ids'])
    else:
        page_ids=list(page.object_list)
    if common and order is None and cached_page is None:
        # Cache only ordering IDs and the display count for 30 seconds.
        # Hydrate through the live active/seller scope below on every visit.
        page_cache.set(page_key,dict(ids=page_ids,count=paginator.count),30)
    # Hydration must retain the global page order: recomputing the window on
    # only these IDs would restart each category's row numbers on every page.
    cards={product.pk:product for product in retail_annotations(found.filter(pk__in=page_ids).order_by())}
    page.object_list=[cards[pk] for pk in page_ids if pk in cards]
    cards_finished=perf_counter()
    for product in page:
        product.public_stock=public_stock_status(product)
        product.has_public_wholesale=False
    chips=[]
    for key in ('country','brand','model','year','engine','category','subgroup','maker','q'):
        if selected[key]:
            label=next((o['label'] for o in facets.get(key,[]) if o['value']==selected[key]),selected[key])
            chips.append(dict(label=label,url=url(**{key:'',**{r:'' for r in RESET.get(key,())}})))
    response=render(request,'catalog/interface/storefront.html',dict(interface_home=True,hide_product_search=True,
        vehicle_navigation=(vehicle_navigation() if not any(selected[k] for k in ('q','stock','category','subgroup','maker')) else None),
        facets=facets,selected=selected,chips=chips,filters_pending=bool(common and 'maker' not in home_facets),products=page,result_count=page.paginator.count,
        previous_url=url(page=page.previous_page_number()) if page.has_previous() else '',
        next_url=url(page=page.next_page_number()) if page.has_next() else '',
        filter_groups=[dict(key=k,title=t,options=facets[k]) for k,t in [('category','Категория'),('subgroup','Подгруппа'),('brand','Марка авто'),('model','Модель'),('maker','Производитель')]]))

    elapsed=perf_counter()-started
    response["Server-Timing"]=f"catalog;dur={elapsed*1000:.1f}"
    if elapsed>1:
        logging.getLogger(__name__).warning(
            'Storefront seconds total=%.3f facets=%.3f cards=%.3f render=%.3f',
            elapsed,facets_finished-started,cards_finished-facets_finished,
            perf_counter()-cards_finished)
    return response
