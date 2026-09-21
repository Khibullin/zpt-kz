from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from catalog.maintenance_kits import (
    add_kit_to_cart,
    build_kit_view,
    kit_years_display,
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


@require_GET
def maintenance_kit_list(request):
    base = _kit_queryset()
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
        add_kit_to_cart(request, kit)
    except CartSellerConflictError as exc:
        messages.error(
            request,
            (
                f'В корзине уже есть товары продавца «{exc.seller_name}». '
                'Сначала оформите текущий заказ или очистите корзину.'
            ),
        )
        return redirect(detail_url)
    except CartModeConflictError as exc:
        messages.error(request, str(exc))
        return redirect(detail_url)
    except ValueError as exc:
        messages.error(request, str(exc) or 'Комплект нельзя добавить в корзину.')
        return redirect(detail_url)
    messages.success(request, 'Комплект добавлен в корзину.')
    return redirect('orders:cart')
