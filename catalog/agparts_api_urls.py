"""Internal, authenticated AG Parts read-only catalog endpoints."""
from django.urls import path

from catalog.agparts_api import product_detail, products, vehicles

urlpatterns = [
    path("products/", products, name="agparts_api_products"),
    path("products/<int:product_id>/", product_detail, name="agparts_api_product_detail"),
    path("vehicles/", vehicles, name="agparts_api_vehicles"),
]
