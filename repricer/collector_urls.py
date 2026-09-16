from django.urls import path

from repricer.collector_api import ingest_batches, listings_manifest

urlpatterns = [
    path("listings/", listings_manifest, name="kaspi_collector_listings"),
    path("batches/", ingest_batches, name="kaspi_collector_batches"),
]
