"""Owner-confirmed request filters, shared by routing, cabinet and public cards."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Brand, CarModel, PartCategory, Seller


def _fold(value):
    return ' '.join(str(value or '').split()).casefold()


def matches_request(filters, req):
    if filters is None:
        return True  # Existing sellers retain their established routing.
    if req.transport_type not in filters.get('transport_types', []):
        return False
    if not filters.get('all_categories') and _fold(req.category) not in {
        _fold(x['name']) for x in filters.get('categories', [])
    }:
        return False
    if filters.get('all_brands'):
        return True
    for brand in filters.get('brands', []):
        if brand['transport_type'] != req.transport_type or _fold(brand['name']) != _fold(req.brand):
            continue
        if brand['all_models'] or _fold(req.model) in {_fold(m['name']) for m in brand['models']}:
            return True
    return False


def filter_queryset(qs, req):
    # JSON snapshots avoid per-seller queries, and keep matching DB-independent.
    allowed = [pk for pk, filters in qs.filter(request_filters__isnull=False)
               .values_list('pk', 'request_filters') if matches_request(filters, req)]
    return qs.filter(Q(request_filters__isnull=True) | Q(pk__in=allowed))


def normalize_filters(data):
    if not isinstance(data, dict):
        raise ValueError('Некорректные настройки.')
    transports = data.get('transport_types')
    if not isinstance(transports, list) or not transports or any(x not in ('car', 'truck') for x in transports):
        raise ValueError('Выберите тип транспорта.')
    for field in ('all_brands', 'all_categories'):
        if type(data.get(field)) is not bool:
            raise ValueError('Выберите все значения или конкретные позиции.')
    result = dict(transport_types=sorted(set(transports)), all_brands=data['all_brands'],
                  all_categories=data['all_categories'], brands=[], categories=[])
    if not result['all_brands']:
        selections = data.get('brands')
        if not isinstance(selections, list) or not selections or len(selections) > 1000:
            raise ValueError('Выберите хотя бы одну марку.')
        seen = set()
        for item in selections:
            if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] in seen:
                raise ValueError('Проверьте выбранные марки.')
            seen.add(item['id'])
            brand = Brand.objects.filter(pk=item['id'], transport_type__in=transports).first()
            if brand is None or type(item.get('all_models')) is not bool:
                raise ValueError('Марка не соответствует выбранному типу транспорта.')
            models = []
            if not item['all_models']:
                ids = item.get('model_ids')
                if not isinstance(ids, list) or not ids or any(type(x) is not int for x in ids):
                    raise ValueError(f'Выберите модели {brand.name} или «Все модели».')
                models = list(CarModel.objects.filter(pk__in=ids, brand=brand,
                              transport_type=brand.transport_type).order_by('name').values('id', 'name'))
                if len(models) != len(set(ids)):
                    raise ValueError(f'Проверьте модели марки {brand.name}.')
            result['brands'].append(dict(id=brand.pk, name=brand.name, transport_type=brand.transport_type,
                                         all_models=item['all_models'], models=models))
    if not result['all_categories']:
        ids = data.get('category_ids')
        if not isinstance(ids, list) or not ids or any(type(x) is not int for x in ids):
            raise ValueError('Выберите хотя бы одну категорию.')
        result['categories'] = list(PartCategory.objects.filter(pk__in=ids).order_by('name').values('id', 'name'))
        if len(result['categories']) != len(set(ids)):
            raise ValueError('Проверьте выбранные категории.')
    return result


def save_filters(seller, data):
    filters = normalize_filters(data)
    with transaction.atomic():
        seller = Seller.objects.select_for_update().get(pk=seller.pk)
        if seller.request_filters and 'telegram_cities' in seller.request_filters:
            filters['telegram_cities'] = seller.request_filters['telegram_cities']
        seller.request_filters = filters
        seller.request_filters_updated_at = timezone.now()
        # Mirror existing public profile fields, without changing direction or geography.
        seller.transport_type = filters['transport_types'][0]
        seller.all_brands = filters['all_brands']
        seller.all_categories = filters['all_categories']
        seller.all_models = filters['all_brands'] or all(b['all_models'] for b in filters['brands'])
        seller.brand = seller.model = seller.category = ''
        seller.brand_fk = seller.model_fk = None
        seller.save(update_fields=['request_filters', 'request_filters_updated_at', 'transport_type',
                                   'all_brands', 'all_models', 'all_categories', 'brand', 'model',
                                   'category', 'brand_fk', 'model_fk'])
        seller.selected_brands.set([b['id'] for b in filters['brands']])
        seller.selected_models.set([m['id'] for b in filters['brands'] for m in b['models']])
        seller.selected_categories.set([c['id'] for c in filters['categories']])
    return seller


def filter_rows(filters):
    if filters is None:
        return []
    return [
        {'label': 'Тип транспорта', 'value': ', '.join({'car': 'Легковые', 'truck': 'Грузовые'}[x] for x in filters['transport_types'])},
        {'label': 'Марки', 'value': 'Все марки' if filters['all_brands'] else ', '.join(dict.fromkeys(b['name'] for b in filters['brands']))},
        {'label': 'Модели', 'value': 'Все модели' if filters['all_brands'] else '; '.join(
            b['name'] + ': ' + ('все модели' if b['all_models'] else ', '.join(m['name'] for m in b['models'])) for b in filters['brands'])},
        {'label': 'Категории запчастей', 'value': 'Все категории' if filters['all_categories'] else ', '.join(c['name'] for c in filters['categories'])},
    ]


def initial_filters(seller):
    if seller.request_filters is not None:
        f = seller.request_filters
        return dict(transport_types=f['transport_types'], all_brands=f['all_brands'],
                    all_categories=f['all_categories'], category_ids=[c['id'] for c in f['categories']],
                    brands=[dict(id=b['id'], all_models=b['all_models'], model_ids=[m['id'] for m in b['models']]) for b in f['brands']])
    # Preselect known profile data; nothing becomes confirmed until Save.
    brands = list(seller.selected_brands.all())
    if not brands and seller.brand_fk_id:
        brands = [seller.brand_fk]
    if not brands and seller.brand:
        brands = list(Brand.objects.filter(name__iexact=seller.brand, transport_type=seller.transport_type))
    models = list(seller.selected_models.all())
    if not models and seller.model_fk_id:
        models = [seller.model_fk]
    if not models and seller.model:
        models = list(CarModel.objects.filter(name__iexact=seller.model, brand__in=brands))
    cats = list(seller.selected_categories.values_list('pk', flat=True))
    if not cats and seller.category:
        cats = list(PartCategory.objects.filter(name__iexact=seller.category).values_list('pk', flat=True))
    return dict(transport_types=[seller.transport_type] if seller.transport_type in ('car', 'truck') else [],
                all_brands=seller.all_brands or not brands, all_categories=seller.all_categories or not cats,
                category_ids=cats, brands=[dict(id=b.pk, all_models=seller.all_models or not any(m.brand_id == b.pk for m in models),
                model_ids=[m.pk for m in models if m.brand_id == b.pk]) for b in brands])
