from core.models import Brand as CoreBrand
from core.models import CarModel as CoreCarModel
from core.models import Country as CoreCountry
from core.vehicle_catalog import VEHICLE_CATALOG


def _sync_to_core(*, transport_type='car'):
    created_countries = 0
    created_brands = 0
    created_models = 0

    for country_name, brands in VEHICLE_CATALOG.items():
        country, country_created = CoreCountry.objects.get_or_create(
            name=country_name,
        )
        if country_created:
            created_countries += 1

        for brand_name, models in brands.items():
            brand, brand_created = CoreBrand.objects.get_or_create(
                country=country,
                name=brand_name,
                transport_type=transport_type,
            )
            if brand_created:
                created_brands += 1

            for model_name in models:
                _, model_created = CoreCarModel.objects.get_or_create(
                    brand=brand,
                    name=model_name,
                    transport_type=transport_type,
                )
                if model_created:
                    created_models += 1

    return {
        'countries': created_countries,
        'brands': created_brands,
        'models': created_models,
    }


def _sync_to_catalog():
    from catalog.models import Brand, CarModel, Country

    created_countries = 0
    created_brands = 0
    created_models = 0

    for country_name, brands in VEHICLE_CATALOG.items():
        country, country_created = Country.objects.get_or_create(
            name=country_name,
        )
        if country_created:
            created_countries += 1

        for brand_name, models in brands.items():
            brand, brand_created = Brand.objects.get_or_create(
                country=country,
                name=brand_name,
            )
            if brand_created:
                created_brands += 1

            for model_name in models:
                _, model_created = CarModel.objects.get_or_create(
                    brand=brand,
                    name=model_name,
                )
                if model_created:
                    created_models += 1

    return {
        'countries': created_countries,
        'brands': created_brands,
        'models': created_models,
    }


def sync_vehicle_catalog(*, transport_type='car'):
    """Синхронизирует справочник в core и catalog (идемпотентно)."""
    return {
        'core': _sync_to_core(transport_type=transport_type),
        'catalog': _sync_to_catalog(),
    }
