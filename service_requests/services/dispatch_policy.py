from service_requests.models import ServiceBroadcastSettings, ServiceSeller

_KNOWN_MODES = frozenset({
    ServiceBroadcastSettings.MODE_OFF,
    ServiceBroadcastSettings.MODE_TEST,
    ServiceBroadcastSettings.MODE_LIVE,
})


def get_service_broadcast_mode():
    """Return the singleton broadcast mode, or OFF when it is missing or unknown.

    This read does not create a settings row.
    """
    row = (
        ServiceBroadcastSettings.objects.order_by('pk')
        .only('mode')
        .first()
    )
    if row is None or row.mode not in _KNOWN_MODES:
        return ServiceBroadcastSettings.MODE_OFF
    return row.mode


def select_service_sellers_for_request(req):
    """Return eligible sellers for one request, ordered for dispatch.

    OFF, a missing settings row, and an unknown mode select nobody.
    District matches are preferred. An ineligible district seller does not
    block the city fallback.
    """
    mode = get_service_broadcast_mode()
    if mode not in (
        ServiceBroadcastSettings.MODE_TEST,
        ServiceBroadcastSettings.MODE_LIVE,
    ):
        return []

    service_names = list(req.services.values_list('name', flat=True))
    if not service_names:
        return []

    sellers = ServiceSeller.objects.filter(
        is_active=True,
        receive_requests=True,
        is_paused=False,
        seller_type=req.service_type,
        city=req.city,
        services__name__in=service_names,
    )
    if mode == ServiceBroadcastSettings.MODE_TEST:
        sellers = sellers.filter(is_test_seller=True)

    sellers = sellers.distinct().order_by('dispatch_priority', 'pk')
    district = (req.district or '').strip()
    if district:
        district_sellers = list(sellers.filter(district=district))
        if district_sellers:
            return district_sellers
    return list(sellers)
