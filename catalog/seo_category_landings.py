from django.db.models import Q
from django.shortcuts import render

from .models import Product
from .seo_landings import SEO_BRAND_LANDINGS
from .views import attach_sellers_to_products
from .wholesale import attach_public_wholesale_flags, public_wholesale_prefetch


OIL_FILTER_LANDING = {
    'title': 'Масляные фильтры в Казахстане — купить фильтр масла | ZPT.KZ',
    'meta_description': (
        'Масляные фильтры для автомобилей в Казахстане. Смотрите актуальные предложения '
        'продавцов ZPT.KZ по марке и артикулу или оставьте заявку на подбор.'
    ),
    'h1': 'Масляные фильтры для автомобилей в Казахстане',
    'intro': (
        'На ZPT.KZ собраны активные предложения масляных фильтров от продавцов автозапчастей. '
        'Для точного выбора сверяйте артикул и применяемость к автомобилю. Если нужного фильтра '
        'нет в каталоге, отправьте заявку — продавцы смогут предложить подходящий вариант.'
    ),
}


def oil_filter_landing(request):
    products = Product.objects.filter(status='active').filter(
        Q(title__icontains='масля') & Q(title__icontains='фильтр')
    ).distinct().select_related(
        'brand',
        'brand__country',
        'car_model',
        'car_model__brand',
        'category',
        'seller_profile',
    ).order_by('-updated_at')
    products = public_wholesale_prefetch(products)
    products = list(products[:48])
    attach_sellers_to_products(products)
    attach_public_wholesale_flags(products)

    brand_landings = [
        {'slug': slug, 'label': spec['brand_name']}
        for slug, spec in SEO_BRAND_LANDINGS.items()
    ]

    return render(request, 'catalog/seo_oil_filter_landing.html', {
        'landing': OIL_FILTER_LANDING,
        'products': products,
        'brand_landings': brand_landings,
    })
