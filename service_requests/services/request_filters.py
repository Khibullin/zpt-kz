"""Confirmed specialization for repair/detailing; legacy routing stays unchanged until Save."""
from django.db import transaction
from django.utils import timezone
from core.models import Brand
from core.services.seller_request_filters import normalize_filters as normalize_vehicle_filters, filter_rows as vehicle_rows, _fold
from service_requests.models import Service, ServiceSeller

SERVICE_NAMES = {'sto': ['Диагностика', 'Двигатель', 'Ходовая часть', 'Тормозная система', 'Рулевое управление', 'КПП / АКПП', 'Электрика', 'Кондиционер / печка', 'Кузовной ремонт', 'Сварочные работы', 'Замена масел / ТО', 'Выездной мастер', 'Другое'], 'detailing': ['Мойка', 'Химчистка салона', 'Полировка кузова', 'Керамика / защитные покрытия', 'Антигравийная плёнка', 'Тонировка', 'Шумоизоляция', 'Перетяжка салона', 'Установка аксессуаров', 'Автозвук / мультимедиа', 'Свет / оптика', 'Внешний тюнинг', 'Другое']}


def service_options(kind):
    return [{'id': i, 'name': name} for i, name in enumerate(SERVICE_NAMES[kind], 1)]


def normalize_filters(data, kind):
    if not isinstance(data, dict):
        raise ValueError('Проверьте настройки.')
    # Reuse authoritative vehicle validation, with our own service vocabulary.
    result = normalize_vehicle_filters({**data, 'all_categories': True})
    if type(data.get('all_categories')) is not bool:
        raise ValueError('Выберите все работы или конкретные услуги.')
    result['all_categories'] = data['all_categories']
    result['service_type'] = kind
    if not result['all_categories']:
        ids = data.get('category_ids')
        if not isinstance(ids, list) or not ids or any(type(x) is not int for x in ids):
            raise ValueError('Выберите хотя бы одну работу или услугу.')
        result['categories'] = [s for s in service_options(kind) if s['id'] in ids]
        if len(result['categories']) != len(set(ids)):
            raise ValueError('Проверьте выбранные работы или услуги.')
    return result


def save_filters(seller, data):
    with transaction.atomic():
        seller = ServiceSeller.objects.select_for_update().get(pk=seller.pk)
        filters = normalize_filters(data, seller.seller_type)
        seller.request_filters = filters
        seller.request_filters_updated_at = timezone.now()
        seller.save(update_fields=['request_filters', 'request_filters_updated_at'])
        names = SERVICE_NAMES[seller.seller_type] if filters['all_categories'] else [s['name'] for s in filters['categories']]
        seller.services.set([Service.objects.get_or_create(name=n)[0] for n in names])
    return seller


def matches_request(seller, req, service_names=None):
    from core.services.test_receivers import test_receiver_decision
    decision = test_receiver_decision(seller, req)
    if decision is not None:
        return decision
    f = seller.request_filters
    if f is None:
        return True
    if f.get('service_type') != req.service_type or seller.seller_type != req.service_type:
        return False
    if req.transport_type not in f.get('transport_types', []):
        return False
    if not f.get('all_categories'):
        names = service_names if service_names is not None else req.services.values_list('name', flat=True)
        requested = {_fold(n) for n in names}
        # Keep the established any-requested-service matching rule.
        if not requested.intersection(_fold(s['name']) for s in f.get('categories', [])):
            return False
    if f.get('all_brands'):
        return True
    for b in f.get('brands', []):
        if b['transport_type'] == req.transport_type and _fold(b['name']) == _fold(req.brand):
            if b['all_models'] or _fold(req.model) in {_fold(m['name']) for m in b['models']}:
                return True
    return False


def filter_rows(filters):
    rows = vehicle_rows(filters)
    if rows:
        rows[-1] = {'label': 'Виды ремонтных работ' if filters['service_type'] == 'sto' else 'Услуги детейлинга и тюнинга',
                    'value': 'Все работы и услуги' if filters['all_categories'] else ', '.join(c['name'] for c in filters['categories'])}
    return rows


def initial_filters(seller):
    f = seller.request_filters
    if f is not None:
        return dict(transport_types=f['transport_types'], all_brands=f['all_brands'], all_categories=f['all_categories'],
                    category_ids=[c['id'] for c in f['categories']],
                    brands=[dict(id=b['id'], all_models=b['all_models'], model_ids=[m['id'] for m in b['models']]) for b in f['brands']])
    # Only preselect known profile data. Unknown transport requires an explicit choice.
    text = _fold(seller.directory_transport)
    transports = [t for t, word in [('car', 'легков'), ('truck', 'грузов')] if word in text]
    known = {_fold(n) for n in seller.services.values_list('name', flat=True)}
    ids = [s['id'] for s in service_options(seller.seller_type) if _fold(s['name']) in known]
    names = {_fold(n.strip()) for n in seller.directory_brands.replace(';', ',').split(',')}
    brands = [dict(id=b.pk, all_models=True, model_ids=[]) for b in Brand.objects.filter(transport_type__in=transports) if _fold(b.name) in names]
    return dict(transport_types=transports, all_brands=not brands, brands=brands, all_categories=not ids, category_ids=ids)
