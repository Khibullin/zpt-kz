from django.db.models import Q
from django.http import Http404
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


SEO_CATEGORY_LANDINGS = {
    'kuzov': {
        'category_name': 'Кузов',
        'title': 'Кузовные запчасти в Казахстане — купить детали кузова | ZPT.KZ',
        'meta_description': (
            'Кузовные запчасти в Казахстане: двери, крылья, бамперы, капоты и другие детали. '
            'Смотрите предложения продавцов ZPT.KZ или оставьте заявку на подбор.'
        ),
        'h1': 'Кузовные запчасти в Казахстане',
        'intro': (
            'На ZPT.KZ собраны активные предложения кузовных деталей от продавцов по Казахстану. '
            'Проверяйте марку, модель и применяемость в карточке товара. Если нужной детали нет, '
            'оставьте заявку — продавцы смогут предложить наличие и цену.'
        ),
        'cta': 'Оставить заявку на кузовную деталь',
        'catalog_label': 'Смотреть кузовные запчасти',
        'section_title': 'Кузовные запчасти в наличии',
        'guide_title': 'Как подобрать кузовную деталь',
        'guide': (
            'Для кузовных деталей особенно важны модель, год выпуска, сторона установки и '
            'комплектация автомобиля. Укажите эти данные в заявке и приложите фото, если оно поможет продавцу.'
        ),
    },
    'dvigatel': {
        'category_name': 'Двигатель',
        'title': 'Запчасти двигателя в Казахстане — детали двигателя | ZPT.KZ',
        'meta_description': (
            'Запчасти двигателя в Казахстане: детали и комплектующие для ремонта двигателя. '
            'Каталог предложений продавцов и заявка на подбор по автомобилю или артикулу.'
        ),
        'h1': 'Запчасти двигателя в Казахстане',
        'intro': (
            'На ZPT.KZ представлены активные предложения деталей двигателя от продавцов '
            'автозапчастей. Сверяйте артикул, двигатель и применяемость перед заказом, '
            'а отсутствующую позицию можно запросить у продавцов одной заявкой.'
        ),
        'cta': 'Оставить заявку на запчасть двигателя',
        'catalog_label': 'Смотреть запчасти двигателя',
        'section_title': 'Запчасти двигателя в наличии',
        'guide_title': 'Как подобрать запчасть двигателя',
        'guide': (
            'Укажите марку, модель, год, объём или код двигателя и известный OEM-номер. '
            'Для многих деталей двигателя одинаковое название не означает одинаковую применяемость.'
        ),
    },
    'tormoza': {
        'category_name': 'Тормозная система',
        'title': 'Тормозные запчасти в Казахстане — тормозная система | ZPT.KZ',
        'meta_description': (
            'Запчасти тормозной системы в Казахстане: колодки, диски, суппорты и другие детали. '
            'Каталог продавцов ZPT.KZ и заявка на подбор.'
        ),
        'h1': 'Запчасти тормозной системы в Казахстане',
        'intro': (
            'На ZPT.KZ собраны активные предложения запчастей тормозной системы. '
            'Перед покупкой проверьте артикул и применяемость к вашему автомобилю. '
            'Если подходящей позиции нет, отправьте заявку продавцам.'
        ),
        'cta': 'Оставить заявку на тормозную запчасть',
        'catalog_label': 'Смотреть тормозные запчасти',
        'section_title': 'Запчасти тормозной системы в наличии',
        'guide_title': 'Как подобрать тормозную запчасть',
        'guide': (
            'Для точного подбора укажите автомобиль, год выпуска и по возможности VIN или OEM-номер. '
            'Размеры дисков, колодок и суппортов могут отличаться даже у одной модели.'
        ),
    },
    'hodovaya-chast': {
        'category_name': 'Ходовая часть',
        'title': 'Запчасти ходовой части в Казахстане — подвеска и ходовая | ZPT.KZ',
        'meta_description': (
            'Запчасти ходовой части и подвески в Казахстане: рычаги, стойки, амортизаторы и другие детали. '
            'Смотрите предложения или отправьте заявку продавцам ZPT.KZ.'
        ),
        'h1': 'Запчасти ходовой части в Казахстане',
        'intro': (
            'На ZPT.KZ представлен каталог запчастей ходовой части и подвески от продавцов '
            'по Казахстану. Сверяйте артикул и применяемость, а если нужной детали нет '
            'в каталоге — оставьте заявку продавцам.'
        ),
        'cta': 'Оставить заявку на ходовую',
        'catalog_label': 'Смотреть запчасти ходовой',
        'section_title': 'Запчасти ходовой части в наличии',
        'guide_title': 'Как подобрать запчасть ходовой части',
        'guide': (
            'Укажите марку, модель, год выпуска и расположение детали — передняя или задняя ось, '
            'левая или правая сторона. OEM-номер значительно снижает риск ошибки.'
        ),
    },
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


def category_landing(request, category_slug):
    spec = SEO_CATEGORY_LANDINGS.get(category_slug)
    if spec is None:
        raise Http404()

    products = (
        Product.objects
        .filter(status='active', category__name__iexact=spec['category_name'])
        .select_related(
            'brand',
            'brand__country',
            'car_model',
            'car_model__brand',
            'category',
            'seller_profile',
        )
        .order_by('-updated_at')
    )
    products = public_wholesale_prefetch(products)
    products = list(products[:48])
    attach_sellers_to_products(products)
    attach_public_wholesale_flags(products)

    brand_landings = [
        {'slug': slug, 'label': landing['brand_name']}
        for slug, landing in SEO_BRAND_LANDINGS.items()
    ]

    return render(request, 'catalog/seo_category_landing.html', {
        'landing': spec,
        'category_slug': category_slug,
        'products': products,
        'brand_landings': brand_landings,
    })
