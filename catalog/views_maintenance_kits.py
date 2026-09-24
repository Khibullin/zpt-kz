from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from catalog.forms import MaintenanceKitCarRequestForm
from catalog.maintenance_kit_requests import send_kit_car_request_notification
from catalog.maintenance_kits import (
    COVER_PARTIAL_CAPTION,
    add_kit_to_cart,
    build_kit_view,
    kit_years_display,
    parse_selected_product_ids,
    published_kits_queryset,
)
from catalog.models import MaintenanceKit
from orders.seller_utils import CartModeConflictError, CartSellerConflictError


def _kit_queryset():
    return published_kits_queryset()


def _filter_kits(queryset, request):
    brand_id = str(request.GET.get('brand') or '').strip()
    model_id = str(request.GET.get('model') or '').strip()
    engine = str(request.GET.get('engine') or '').strip()
    if brand_id.isdigit():
        queryset = queryset.filter(brand_id=int(brand_id))
    if model_id.isdigit():
        queryset = queryset.filter(car_model_id=int(model_id))
    if engine:
        queryset = queryset.filter(engine=engine)
    return queryset, brand_id, model_id, engine


def _search_query(request):
    brand_query = str(request.GET.get('q_brand') or '').strip()[:100]
    model_query = str(request.GET.get('q_model') or '').strip()[:100]
    return brand_query, model_query


def _apply_car_search(queryset, brand_query, model_query):
    """Match published kits by typed brand/model. Does not search Product catalog."""
    active = bool(brand_query or model_query)
    if not active:
        return queryset, False
    if brand_query:
        queryset = queryset.filter(brand__name__icontains=brand_query)
    if model_query:
        queryset = queryset.filter(car_model__name__icontains=model_query)
    return queryset, True


def _missing_car_url(brand_query='', model_query=''):
    url = reverse('maintenance_kit_missing_car')
    params = {}
    if brand_query:
        params['brand'] = brand_query
    if model_query:
        params['model'] = model_query
    if params:
        return f'{url}?{urlencode(params)}'
    return url


def _selection_summary(brand_id, model_id, engine, brands, models, search_active, brand_query, model_query):
    parts = []
    if search_active:
        searched = ' '.join(part for part in (brand_query, model_query) if part)
        if searched:
            parts.append(f'Поиск: {searched}')
    else:
        if brand_id.isdigit():
            brand = next((item for item in brands if str(item.id) == brand_id), None)
            if brand:
                parts.append(f'Марка: {brand.name}')
        if model_id.isdigit():
            model = next((item for item in models if str(item.id) == model_id), None)
            if model:
                parts.append(f'Модель: {model.name}')
        if engine:
            parts.append(f'Двигатель: {engine}')
    return ' · '.join(parts)


def _picker_choices(queryset, selected_brand, selected_model):
    brands = []
    seen_brands = set()
    models = []
    seen_models = set()
    engines = []
    seen_engines = set()
    for kit in queryset:
        if kit.brand_id not in seen_brands:
            seen_brands.add(kit.brand_id)
            brands.append(kit.brand)
        if selected_brand and str(kit.brand_id) != str(selected_brand):
            continue
        if kit.car_model_id not in seen_models:
            seen_models.add(kit.car_model_id)
            models.append(kit.car_model)
        if selected_model and str(kit.car_model_id) != str(selected_model):
            continue
        engine = (kit.engine or '').strip()
        if engine and engine not in seen_engines:
            seen_engines.add(engine)
            engines.append(engine)
    brands.sort(key=lambda brand: brand.name.lower())
    models.sort(key=lambda model: model.name.lower())
    engines.sort()
    return brands, models, engines


def _cart_error_redirect(request, detail_url, exc):
    if isinstance(exc, CartSellerConflictError):
        messages.error(
            request,
            (
                f'В корзине уже есть товары продавца «{exc.seller_name}». '
                'Сначала оформите текущий заказ или очистите корзину.'
            ),
        )
        return redirect(detail_url)
    if isinstance(exc, CartModeConflictError):
        messages.error(request, str(exc))
        return redirect(detail_url)
    messages.error(request, str(exc) or 'Комплект нельзя добавить в корзину.')
    return redirect(detail_url)


@require_GET
def maintenance_kit_list(request):
    base = _kit_queryset()
    brand_query, model_query = _search_query(request)
    search_qs, search_active = _apply_car_search(base, brand_query, model_query)
    if search_active:
        kits = search_qs
        brand_id, model_id, engine = '', '', ''
    else:
        kits, brand_id, model_id, engine = _filter_kits(base, request)
    brands, models, engines = _picker_choices(base, brand_id, model_id)
    kit_views = [build_kit_view(kit, request) for kit in kits]
    return render(request, 'catalog/maintenance_kit_list.html', {
        'kits': kit_views,
        'brands': brands,
        'models': models,
        'engines': engines,
        'selected_brand': brand_id,
        'selected_model': model_id,
        'selected_engine': engine,
        'search_active': search_active,
        'search_brand': brand_query,
        'search_model': model_query,
        'selection_summary': _selection_summary(
            brand_id, model_id, engine, brands, models,
            search_active, brand_query, model_query,
        ),
        'missing_car_url': _missing_car_url(brand_query, model_query),
        'page_title': 'Комплекты ТО — купить расходники для обслуживания | ZPT.KZ',
        'page_description': (
            'Готовые комплекты ТО для автомобилей в Казахстане: фильтры и свечи '
            'под марку, модель и двигатель. Смотрите состав и наличие на ZPT.KZ.'
        ),
    })


@require_GET
def maintenance_kit_detail(request, slug):
    kit = get_object_or_404(_kit_queryset(), slug=slug)
    view = build_kit_view(kit, request)
    years = kit_years_display(kit)
    return render(request, 'catalog/maintenance_kit_detail.html', {
        'kit': kit,
        'kit_view': view,
        'years': years,
        'cover_partial_caption': COVER_PARTIAL_CAPTION,
        'page_title': f'{kit.name} — комплект ТО | ZPT.KZ',
        'page_description': (
            f'Комплект ТО {kit.brand.name} {kit.car_model.name}'
            + (f' {kit.engine}' if kit.engine else '')
            + '. Состав, цены и наличие расходников на ZPT.KZ.'
        ),
    })


@require_POST
def maintenance_kit_add_to_cart(request, slug):
    kit = get_object_or_404(
        MaintenanceKit.objects.filter(is_active=True),
        slug=slug,
    )
    detail_url = reverse('maintenance_kit_detail', kwargs={'slug': kit.slug})
    try:
        selected_ids = parse_selected_product_ids(request.POST.getlist('item'))
        add_kit_to_cart(request, kit, selected_ids=selected_ids)
    except (CartSellerConflictError, CartModeConflictError, ValueError) as exc:
        return _cart_error_redirect(request, detail_url, exc)
    messages.success(request, 'Выбранные позиции добавлены в корзину.')
    return redirect('orders:cart')


@require_http_methods(['GET', 'POST'])
def maintenance_kit_missing_car(request):
    if request.method == 'POST':
        form = MaintenanceKitCarRequestForm(request.POST)
    else:
        initial = {}
        brand = str(request.GET.get('brand') or '').strip()[:100]
        model = str(request.GET.get('model') or '').strip()[:100]
        if brand:
            initial['brand'] = brand
        if model:
            initial['model'] = model
        form = MaintenanceKitCarRequestForm(initial=initial)
    if request.method == 'POST':
        if form.is_valid():
            from core.services.public_rate_limit import home_parts_rate_limit_allowed

            phone = form.cleaned_data.get('phone') or ''
            if not home_parts_rate_limit_allowed(request, phone):
                messages.error(
                    request,
                    'Слишком много запросов. Попробуйте позже.',
                )
            else:
                car_request = form.save()
                send_kit_car_request_notification(car_request, request)
                messages.success(
                    request,
                    'Заявка получена. Мы свяжемся с вами в WhatsApp после проверки данных автомобиля.',
                )
                return redirect('maintenance_kit_missing_car')
    return render(request, 'catalog/maintenance_kit_missing_car.html', {
        'form': form,
        'page_title': 'Нет моего автомобиля — комплекты ТО | ZPT.KZ',
        'page_description': (
            'Оставьте автомобиль и WhatsApp — мы учтём спрос и свяжемся, '
            'когда состав ТО будет проверен.'
        ),
    })
