from django.db.models import Q
from django.http import Http404
from django.shortcuts import render

from .models import Brand, Product
from .views import attach_sellers_to_products
from .wholesale import attach_public_wholesale_flags, public_wholesale_prefetch


SEO_BRAND_LANDINGS = {
    'changan': {
        'brand_name': 'Changan',
        'title': 'Запчасти Changan в Казахстане — купить с доставкой | ZPT.KZ',
        'meta_description': (
            'Автозапчасти Changan в Казахстане: фильтры, свечи, тормозные и другие детали. '
            'Смотрите товары в наличии или оставьте заявку продавцам ZPT.KZ.'
        ),
        'h1': 'Автозапчасти Changan в Казахстане',
        'intro': (
            'На ZPT.KZ собраны запчасти и расходные материалы для автомобилей Changan '
            'от продавцов в Казахстане. Сравнивайте предложения по артикулу и применяемости, '
            'а если нужной детали нет в каталоге — отправьте одну заявку продавцам.'
        ),
    },
    'chery': {
        'brand_name': 'Chery',
        'title': 'Запчасти Chery в Казахстане — каталог и цены | ZPT.KZ',
        'meta_description': (
            'Запчасти Chery в Казахстане: фильтры, свечи, тормозные детали и расходники. '
            'Каталог предложений продавцов и заявка на подбор через ZPT.KZ.'
        ),
        'h1': 'Автозапчасти Chery в Казахстане',
        'intro': (
            'Каталог ZPT.KZ помогает искать запчасти для Chery по названию, артикулу и '
            'применяемости. Здесь собраны предложения продавцов, а для отсутствующей детали '
            'можно оставить заявку и получить ответы в WhatsApp.'
        ),
    },
    'haval': {
        'brand_name': 'Haval',
        'title': 'Запчасти Haval в Казахстане — каталог автозапчастей | ZPT.KZ',
        'meta_description': (
            'Автозапчасти Haval в Казахстане: фильтры, расходники и другие детали. '
            'Проверяйте наличие в каталоге ZPT.KZ или отправляйте заявку продавцам.'
        ),
        'h1': 'Автозапчасти Haval в Казахстане',
        'intro': (
            'На этой странице собраны активные предложения запчастей для Haval на ZPT.KZ. '
            'Можно перейти в карточку товара, уточнить наличие у продавца или отправить заявку, '
            'если нужной позиции пока нет в каталоге.'
        ),
    },
    'zeekr': {
        'brand_name': 'Zeekr',
        'title': 'Запчасти Zeekr в Казахстане — купить автозапчасти | ZPT.KZ',
        'meta_description': (
            'Запчасти Zeekr в Казахстане: каталог доступных деталей и расходников на ZPT.KZ. '
            'Если нужного товара нет, отправьте заявку продавцам через WhatsApp.'
        ),
        'h1': 'Автозапчасти Zeekr в Казахстане',
        'intro': (
            'ZPT.KZ собирает предложения продавцов запчастей для Zeekr в одном месте. '
            'Проверяйте карточки товаров и применяемость, а для редких позиций используйте '
            'заявку на подбор запчасти по Казахстану.'
        ),
    },
}


def brand_landing(request, brand_slug):
    landing = SEO_BRAND_LANDINGS.get(brand_slug)
    if landing is None:
        raise Http404('SEO brand landing not found')

    brand = Brand.objects.filter(name__iexact=landing['brand_name']).order_by('pk').first()
    if brand is None:
        raise Http404('Brand not found')

    products = Product.objects.filter(status='active').filter(
        Q(brand=brand)
        | Q(car_model__brand=brand)
        | Q(selected_brands=brand)
        | Q(selected_models__brand=brand)
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

    related_landings = [
        {
            'slug': slug,
            'label': spec['brand_name'],
        }
        for slug, spec in SEO_BRAND_LANDINGS.items()
        if slug != brand_slug
    ]

    return render(request, 'catalog/seo_brand_landing.html', {
        'landing': landing,
        'brand': brand,
        'products': products,
        'related_landings': related_landings,
    })
