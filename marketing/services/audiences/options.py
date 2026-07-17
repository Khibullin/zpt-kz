from __future__ import annotations

from core.models import BuyerCityInterest, BuyerContact
from marketing.services.audiences.constants import (
    GROUP_BUYERS,
    GROUP_SELLERS,
    GROUP_SERVICE_PROVIDERS,
    SUBTYPE_DETAILING,
    SUBTYPE_MARKETPLACE_PAID,
    SUBTYPE_MARKETPLACE_SELLERS,
    SUBTYPE_PARTS_REQUESTS,
    SUBTYPE_REQUEST_SELLERS,
    SUBTYPE_SERVICE_REQUESTS,
    SUBTYPE_STO,
)
from marketing.services.audiences.filters import service_ids_for_seller_type
from marketing.services.contacts import MarketingContact, filter_options
from service_requests.models import Service, ServiceSeller


def _sorted_distinct(values) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or '').strip()}, key=str.casefold)


def build_audience_filter_options(
    *,
    contact_group: str,
    contact_subtype: str,
    registry: dict[str, MarketingContact],
) -> dict:
    base = filter_options(registry)
    options: dict = {
        'primary_cities': _sorted_distinct(
            BuyerContact.objects.exclude(primary_city='').values_list('primary_city', flat=True),
        ),
        'search_cities': _sorted_distinct(
            BuyerCityInterest.objects.exclude(city='').values_list('city', flat=True),
        ),
        'services_sto': list(
            Service.objects.filter(serviceseller__seller_type='sto')
            .distinct()
            .order_by('name')
            .values('id', 'name'),
        ),
        'services_detailing': list(
            Service.objects.filter(serviceseller__seller_type='detailing')
            .distinct()
            .order_by('name')
            .values('id', 'name'),
        ),
        'services_sto_ids': service_ids_for_seller_type('sto'),
        'services_detailing_ids': service_ids_for_seller_type('detailing'),
    }
    options.update(base)

    if contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_PARTS_REQUESTS:
        options['show_primary_cities'] = True
        options['show_search_cities'] = True
        options['show_search_scopes'] = True
        options['show_transport_types'] = True
        options['show_brands'] = True
        options['show_models'] = True
        options['show_categories'] = True
        options['show_request_counts'] = True
        options['show_cities'] = False
        options['show_services'] = False
    elif contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_MARKETPLACE_PAID:
        options['show_cities'] = True
        options['show_orders_counts'] = True
    elif contact_group == GROUP_BUYERS and contact_subtype == SUBTYPE_SERVICE_REQUESTS:
        options['show_cities'] = True
        options['show_district'] = True
        options['show_service_type'] = True
        options['show_services'] = True
        options['show_brands'] = True
        options['show_models'] = True
        options['show_all_services'] = True
    elif contact_group == GROUP_SELLERS and contact_subtype == SUBTYPE_REQUEST_SELLERS:
        options['show_cities'] = True
        options['show_transport_types'] = True
        options['show_brands'] = True
        options['show_models'] = True
        options['show_categories'] = True
        options['show_receive_requests'] = True
        options['show_is_paused'] = True
    elif contact_group == GROUP_SELLERS and contact_subtype == SUBTYPE_MARKETPLACE_SELLERS:
        options['show_cities'] = True
        options['show_products'] = True
        options['show_profile'] = True
    elif contact_group == GROUP_SELLERS:
        options['show_cities'] = True
        options['show_transport_types'] = True
        options['show_brands'] = True
        options['show_models'] = True
        options['show_categories'] = True
        options['show_receive_requests'] = True
        options['show_is_paused'] = True
        options['show_products'] = True
        options['show_profile'] = True
    elif contact_group == GROUP_SERVICE_PROVIDERS and contact_subtype == SUBTYPE_STO:
        options['show_cities'] = True
        options['show_district'] = True
        options['show_services'] = True
        options['show_services_sto_only'] = True
        options['show_receive_requests'] = True
        options['show_is_paused'] = True
        options['show_profile'] = True
    elif contact_group == GROUP_SERVICE_PROVIDERS and contact_subtype == SUBTYPE_DETAILING:
        options['show_cities'] = True
        options['show_district'] = True
        options['show_services'] = True
        options['show_services_detailing_only'] = True
        options['show_receive_requests'] = True
        options['show_is_paused'] = True
        options['show_profile'] = True
    elif contact_group == GROUP_SERVICE_PROVIDERS:
        options['show_cities'] = True
        options['show_district'] = True
        options['show_service_type'] = True
        options['show_services'] = True
        options['show_all_services'] = True
        options['show_receive_requests'] = True
        options['show_is_paused'] = True
        options['show_profile'] = True

    return options


__all__ = ['build_audience_filter_options']
