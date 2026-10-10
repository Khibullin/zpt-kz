"""AG Parts curated store page using existing products, without duplicate catalog rows."""
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from catalog.models import Product, SellerProfile
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
