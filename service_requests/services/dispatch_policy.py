from django.db.models import Q
from service_requests.services.request_filters import matches_request
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
    from core.services.manual_request_processing import manual_processing_enabled
    from core.models import BroadcastSettings
    if manual_processing_enabled() or BroadcastSettings.load().emergency_stop:
        return []
    mode = get_service_broadcast_mode()
    if mode not in (
        ServiceBroadcastSettings.MODE_TEST,
        ServiceBroadcastSettings.MODE_LIVE,
    ):
        return []

    service_names = list(req.services.values_list('name', flat=True))
    if not service_names:
        return []

    from core.services.test_receivers import universal_test_q, test_receiver_decision
    sellers = ServiceSeller.objects.filter(
        is_active=True,
        receive_requests=True,
        is_paused=False,
    ).filter(universal_test_q(req) | (Q(seller_type=req.service_type, city=req.city) &
             (Q(request_filters__isnull=False) | Q(services__name__in=service_names))))
    if mode == ServiceBroadcastSettings.MODE_TEST:
        sellers = sellers.filter(is_test_seller=True)

    sellers = sellers.distinct().order_by('dispatch_priority', 'pk')
    sellers = [s for s in sellers if matches_request(s, req, service_names)]
    district = (req.district or '').strip()
    if district:
        district_sellers = [s for s in sellers if s.district == district and test_receiver_decision(s, req) is not True]
        if district_sellers:
            return [s for s in sellers if s.district == district or test_receiver_decision(s, req) is True]
    return list(sellers)
