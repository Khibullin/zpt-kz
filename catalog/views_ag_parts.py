"""AG Parts curated store page using existing products, without duplicate catalog rows."""
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from catalog.models import Brand, CarModel, Product, SellerProfile
from catalog.commercial import filter_products_by_vehicle
from catalog.views import attach_sellers_to_products
from catalog.wholesale import attach_public_wholesale_flags, public_wholesale_prefetch


@require_GET
def ag_parts_store(request):
    seller = get_object_or_404(SellerProfile, slug="ag-parts")
    products = Product.objects.filter(
        seller_profile=seller,
        status="active",
    ).select_related(
        "brand", "car_model", "category", "seller_profile",
    ).prefetch_related("kaspi_listings").order_by("title", "id")
    products = list(public_wholesale_prefetch(products))
    attach_sellers_to_products(products)
    attach_public_wholesale_flags(products)
    return render(request, "catalog/ag_parts_store.html", {
        "seller": seller,
        "products": products,
        "page_title": "Наши товары AG Parts — автозапчасти | ZPT.KZ",
        "page_description": (
            "Ассортимент магазина AG Parts на ZPT.KZ. "
            "Подбор фильтров и запчастей, покупка через Kaspi при наличии прямой ссылки."
        ),
    })


@require_GET
def ag_parts_filter_finder(request):
    """Two-step make/model picker for AG Parts' existing, structured fitment."""
    seller = get_object_or_404(SellerProfile, slug="ag-parts")
    base_products = Product.objects.filter(
        seller_profile=seller,
        status="active",
    ).select_related(
        "brand", "car_model", "car_model__brand", "category", "seller_profile",
    ).prefetch_related(
        "selected_models__brand", "selected_brands", "kaspi_listings",
    )

    # Offer only make/model pairs already recorded on AG Parts products.
    model_ids = set()
    for product in base_products:
        if product.car_model_id:
            model_ids.add(product.car_model_id)
        model_ids.update(model.pk for model in product.selected_models.all())

    model_options = list(
        CarModel.objects.filter(pk__in=model_ids)
        .select_related("brand")
        .order_by("brand__name", "name")
        .values("id", "name", "brand_id", "brand__name")
    )
    brand_ids = {item["brand_id"] for item in model_options}
    brands = list(Brand.objects.filter(pk__in=brand_ids).order_by("name"))

    brand_id = request.GET.get("brand", "").strip()
    model_id = request.GET.get("model", "").strip()
    allowed_brand_ids = {str(brand.pk) for brand in brands}
    if brand_id not in allowed_brand_ids:
        brand_id = ""
        model_id = ""

    allowed_models = {
        str(item["id"]): item
        for item in model_options
        if str(item["brand_id"]) == brand_id
    }
    if model_id not in allowed_models:
        model_id = ""

    products = []
    if brand_id and model_id:
        matching = filter_products_by_vehicle(
            base_products,
            brand_id=brand_id,
        )
        matching = filter_products_by_vehicle(matching, model_id=model_id)
        products = list(
            public_wholesale_prefetch(matching)
            .order_by("title", "id")
        )
        attach_sellers_to_products(products)
        attach_public_wholesale_flags(products)

    return render(request, "catalog/ag_parts_filter_finder.html", {
        "seller": seller,
        "brands": brands,
        "model_options": model_options,
        "selected_brand": brand_id,
        "selected_model": model_id,
        "products": products,
        "has_selection": bool(brand_id and model_id),
        "page_title": "Подбор фильтров AG Parts по автомобилю | ZPT.KZ",
        "page_description": (
            "Выберите марку и модель автомобиля, чтобы посмотреть фильтры и свечи "
            "AG Parts с подходящей применяемостью."
        ),
    })
