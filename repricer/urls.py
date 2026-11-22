from django.urls import path

from .views import repricer_dashboard, repricer_product, repricer_save_price, repricer_save_prices, repricer_export
from .manual_collection import manual_start, manual_step, manual_pause, manual_resume
from .kaspi_upload import upload_kaspi_assortment


app_name = "repricer"

urlpatterns = [
    path("", repricer_dashboard, name="dashboard"),
    path("product/<int:listing_id>/", repricer_product, name="product"),
    path("product/<int:listing_id>/save-price/", repricer_save_price, name="save_price"),
    path("save-prices/", repricer_save_prices, name="save_prices"),
    path("export/", repricer_export, name="export"),
    path("upload-kaspi/", upload_kaspi_assortment, name="upload_kaspi"),
    path("manual/start/", manual_start, name="manual_start"),
    path("manual/<int:run_id>/step/", manual_step, name="manual_step"),
    path("manual/<int:run_id>/pause/", manual_pause, name="manual_pause"),
    path("manual/<int:run_id>/resume/", manual_resume, name="manual_resume"),
]
